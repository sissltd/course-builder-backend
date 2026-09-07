from django.db import transaction
from rest_framework import serializers

from api.quizzes.models import Question, QuestionOption, Quiz


class QuestionOptionSerializer(serializers.ModelSerializer):
    """Serialize one answer option nested under its question."""

    class Meta:
        model = QuestionOption
        fields = ["id", "option_text", "is_correct", "explanation", "order"]


class QuestionSerializer(serializers.ModelSerializer):
    """Serialize a question with its options nested."""

    options = QuestionOptionSerializer(many=True, required=False)

    class Meta:
        model = Question
        fields = [
            "id",
            "quiz",
            "question_text",
            "question_type",
            "points",
            "model_response_guide",
            "order",
            "options",
        ]
        # Optional so questions can be nested inline under a quiz-create
        # payload, where the parent quiz does not exist yet; the standalone
        # questions endpoint enforces presence in perform_create.
        extra_kwargs = {
            "quiz": {"required": False},
            "points": {"min_value": 1},
        }

    def get_unique_together_validators(self):
        # The (quiz, order) DB constraint is checked explicitly in
        # validate() - DRF's auto UniqueTogetherValidator would demand
        # `quiz` be present in the input even for questions nested inline
        # under a quiz-create payload, where the parent supplies it.
        return []

    def validate(self, attrs):
        """Enforce option requirements per question type and (quiz, order)
        uniqueness with a clean field-scoped error."""

        question_type = attrs.get(
            "question_type", getattr(self.instance, "question_type", None)
        )
        supplied_options = attrs.get("options")
        if supplied_options is None:
            options = (
                list(self.instance.options.all()) if self.instance is not None else []
            )
            option_orders = [option.order for option in options]
            correct_option_count = sum(option.is_correct for option in options)
        else:
            options = supplied_options
            option_orders = [option.get("order", 0) for option in options]
            correct_option_count = sum(
                option.get("is_correct", False) for option in options
            )

        if len(option_orders) != len(set(option_orders)):
            raise serializers.ValidationError(
                {"options": "Option order values must be unique within a question."}
            )

        if question_type == Question.TypeChoices.MULTIPLE_CHOICE and not options:
            raise serializers.ValidationError(
                {"options": "MULTIPLE_CHOICE questions require at least one option."}
            )
        if (
            question_type == Question.TypeChoices.MULTIPLE_CHOICE
            and correct_option_count != 1
        ):
            raise serializers.ValidationError(
                {
                    "options": (
                        "MULTIPLE_CHOICE questions require exactly one correct option."
                    )
                }
            )
        if question_type == Question.TypeChoices.ESSAY and options:
            raise serializers.ValidationError(
                {"options": "ESSAY questions cannot have options."}
            )

        quiz = attrs.get("quiz") or getattr(self.instance, "quiz", None)
        order = attrs.get("order", getattr(self.instance, "order", None))
        if quiz is not None and order is not None:
            duplicates = Question.objects.filter(quiz=quiz, order=order)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {
                        "order": (
                            "A question with this order already exists in this quiz."
                        )
                    }
                )
        return attrs

    def _create_or_update_options(self, question, options_data):
        """Replace the question's options with the supplied set atomically."""

        question.options.all().delete()
        QuestionOption.objects.bulk_create(
            [QuestionOption(question=question, **option) for option in options_data]
        )

    @transaction.atomic
    def create(self, validated_data):
        """Create the question plus any nested options."""

        options_data = validated_data.pop("options", [])
        question = Question.objects.create(**validated_data)
        self._create_or_update_options(question, options_data)
        return question

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update the question, replacing options when supplied."""

        options_data = validated_data.pop("options", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if options_data is not None:
            self._create_or_update_options(instance, options_data)
        return instance


class QuizSerializer(serializers.ModelSerializer):
    """Serialize a quiz with its questions nested for creator/admin screens."""

    questions = QuestionSerializer(many=True, required=False)

    class Meta:
        model = Quiz
        fields = [
            "id",
            "level",
            "title",
            "description",
            "lesson",
            "module",
            "course",
            "passing_score",
            "time_limit_minutes",
            "attempts_allowed",
            "shuffle_questions",
            "randomize_options",
            "questions",
        ]
        extra_kwargs = {"passing_score": {"max_value": 100}}

    def validate(self, attrs):
        """Require the parent FK to match the declared level (exactly one)."""

        instance = self.instance
        level = attrs.get("level") or getattr(instance, "level", None)
        resolved = {
            "lesson": attrs.get("lesson", getattr(instance, "lesson", None)),
            "module": attrs.get("module", getattr(instance, "module", None)),
            "course": attrs.get("course", getattr(instance, "course", None)),
        }
        if level is not None:
            expected = level.lower()
            provided = [field for field, value in resolved.items() if value]
            unexpected = [field for field in provided if field != expected]
            if unexpected:
                raise serializers.ValidationError(
                    f"A {level}-level quiz must set only the '{expected}' field."
                )
            if expected not in provided:
                raise serializers.ValidationError(
                    {
                        "non_field_errors": (
                            f"A {level}-level quiz requires the '{expected}' field."
                        )
                    }
                )

        questions = attrs.get("questions")
        if questions is not None:
            question_orders = [question.get("order", 0) for question in questions]
            if len(question_orders) != len(set(question_orders)):
                raise serializers.ValidationError(
                    {
                        "questions": (
                            "Question order values must be unique within a quiz."
                        )
                    }
                )
        return attrs

    def _create_questions(self, quiz, questions_data):
        """Create nested questions (and their options) for a new quiz."""

        for question_data in questions_data:
            options_data = question_data.pop("options", [])
            question = Question.objects.create(
                quiz=quiz,
                created_by=quiz.created_by,
                updated_by=quiz.updated_by,
                **question_data,
            )
            QuestionOption.objects.bulk_create(
                [QuestionOption(question=question, **option) for option in options_data]
            )

    @transaction.atomic
    def create(self, validated_data):
        """Create the quiz plus any nested questions/options."""

        questions_data = validated_data.pop("questions", [])
        quiz = Quiz.objects.create(**validated_data)
        self._create_questions(quiz, questions_data)
        return quiz

    @transaction.atomic
    def update(self, instance, validated_data):
        """Update quiz fields; nested questions are managed via Question endpoints."""

        questions_data = validated_data.pop("questions", None)
        if questions_data is not None:
            raise serializers.ValidationError(
                {"questions": "Update questions via the questions endpoints."}
            )
        return super().update(instance, validated_data)
