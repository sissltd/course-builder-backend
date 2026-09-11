# Quiz Assessment Frontend Implementation

This guide explains how the frontend should implement Figma quiz/question screens
for course assessments.

Use the course Assessment endpoints for the Figma Course Builder quiz UI. Do not
use the legacy `/api/v1/quizzes/` and `/api/v1/questions/` endpoints for this
builder flow.

## Endpoints

Each course location has one assessment. `GET` returns `404` until the assessment
has been saved once. `PUT` creates or replaces the complete assessment.

| Location | Endpoint |
| --- | --- |
| Lesson quiz | `GET/PUT /api/v1/courses/{course_id}/modules/{module_id}/lessons/{lesson_id}/assessment/` |
| Module quiz | `GET/PUT /api/v1/courses/{course_id}/modules/{module_id}/assessment/` |
| Final assessment | `GET/PUT /api/v1/courses/{course_id}/final-assessment/` |

The course must be in `DRAFT` before the creator can save assessments.

## Creator Save Payload

The frontend saves the quiz definition. These fields are for the creator/editor,
not the learner attempt.

```json
{
  "title": "Variables quiz",
  "questions": [
    {
      "type": "SINGLE_CHOICE",
      "question": "Which variable name is valid?",
      "points": 10,
      "options": [
        {
          "text": "2value",
          "explanation": "It starts with a digit."
        },
        {
          "text": "user_name",
          "explanation": "Underscores are valid."
        }
      ],
      "correct_index": 1
    },
    {
      "type": "MULTIPLE_CHOICE",
      "question": "Which values are Python collections?",
      "points": 8,
      "options": [
        {
          "text": "list",
          "explanation": "Lists are collections."
        },
        {
          "text": "tuple",
          "explanation": "Tuples are collections."
        },
        {
          "text": "function",
          "explanation": "Functions are not collections."
        }
      ],
      "correct_indices": [0, 1]
    },
    {
      "type": "ESSAY",
      "question": "Explain why descriptive names matter.",
      "points": 12,
      "expected_answer": "They make code easier to read and maintain.",
      "explanation": "Award credit for linking descriptive names to readability and maintenance."
    }
  ]
}
```

## Question Type Rules

### Single Choice


Creator save fields:

```ts
type SingleChoiceQuestion = {
  type: "SINGLE_CHOICE";
  question: string;
  points: number;
  options: { text: string; explanation: string }[];
  correct_index: number;
};
```

Rules:

- `options` must contain 2 to 6 options.
- `correct_index` is zero-based.
- Exactly one answer is correct.
- Do not send `correct_indices`.
- Do not send top-level `expected_answer`.
- Do not send top-level `explanation`; explanations live inside each option.

Learner answer shape:

```ts
{
  question_index: number;
  type: "SINGLE_CHOICE";
  selected_index: number;
}
```

### Multiple Choice

Use checkboxes in the UI.

Creator save fields:

```ts
type MultipleChoiceQuestion = {
  type: "MULTIPLE_CHOICE";
  question: string;
  points: number;
  options: { text: string; explanation: string }[];
  correct_indices: number[];
};
```

Rules:

- `options` must contain 2 to 6 options.
- `correct_indices` is zero-based.
- `correct_indices` must contain one or more unique indexes.
- Do not send `correct_index`.
- Do not send top-level `expected_answer`.
- Do not send top-level `explanation`; explanations live inside each option.

Learner answer shape:

```ts
{
  question_index: number;
  type: "MULTIPLE_CHOICE";
  selected_indices: number[];
}
```

### Essay

Use a text area in the learner UI.

Creator save fields:

```ts
type EssayQuestion = {
  type: "ESSAY";
  question: string;
  points: number;
  expected_answer: string;
  explanation: string;
};
```

Rules:

- `expected_answer` is the reference answer.
- `explanation` is grading guidance or review guidance.
- Do not send `options`.
- Do not send `correct_index`.
- Do not send `correct_indices`.

Learner answer shape:

```ts
{
  question_index: number;
  type: "ESSAY";
  answer_text: string;
}
```

Essay questions do not use an index for the learner's answer. The learner answer
is the plain text value from the text area.

## Rendering Logic

Use `question.type` to choose the control:

```ts
function controlForQuestion(question: AssessmentQuestion) {
  if (question.type === "SINGLE_CHOICE") return "radio";
  if (question.type === "MULTIPLE_CHOICE") return "checkbox";
  if (question.type === "ESSAY") return "textarea";
  return "unsupported";
}
```

Hide the creator answer fields from learners:

- hide `correct_index`
- hide `correct_indices`
- hide `expected_answer`
- hide `explanation`
- hide option `explanation` until review/feedback mode

## Learner Attempt Payload

The current Assessment endpoints store quiz definitions. They do not persist
learner attempts.

If the frontend needs to let learners take a quiz before a backend attempt
endpoint exists, keep the learner answer shape separate from the creator save
shape:

```json
{
  "assessment": "ASSESSMENT_ID",
  "answers": [
    {
      "question_index": 0,
      "type": "SINGLE_CHOICE",
      "selected_index": 1
    },
    {
      "question_index": 1,
      "type": "MULTIPLE_CHOICE",
      "selected_indices": [0, 1]
    },
    {
      "question_index": 2,
      "type": "ESSAY",
      "answer_text": "Descriptive names make code easier to understand, debug, and maintain."
    }
  ]
}
```

Recommended backend contract for a future learner attempt endpoint:

```http
POST /api/v1/assessments/{assessment_id}/attempts/
```

```json
{
  "answers": [
    {
      "question_index": 0,
      "selected_index": 1
    },
    {
      "question_index": 1,
      "selected_indices": [0, 1]
    },
    {
      "question_index": 2,
      "answer_text": "Descriptive names make code easier to understand, debug, and maintain."
    }
  ]
}
```

The backend should grade single choice and multiple choice automatically from
the saved indexes. Essay grading should compare `answer_text` against the saved
`expected_answer` and use `explanation` as grading guidance.

## Legacy Quiz And Question APIs

The legacy endpoints are still available:

- `POST/GET /api/v1/quizzes/`
- `POST/GET/PATCH/DELETE /api/v1/questions/`

Use them only for relational quiz records, not the Figma Course Builder
assessment flow.

When creating or updating a relational quiz, exactly one parent must be set.
Send the inactive parent fields as `null`, especially on `PUT` or `PATCH`
because omitted fields keep their current value during update.

```json
{
  "level": "LESSON",
  "title": "Lorem 1 Quiz",
  "description": "",
  "lesson": "LESSON_ID",
  "module": null,
  "course": null,
  "passing_score": 70,
  "time_limit_minutes": 0,
  "attempts_allowed": 3,
  "shuffle_questions": false,
  "randomize_options": false
}
```

Legacy `/api/v1/questions/` shape:

```json
{
  "quiz": "QUIZ_ID",
  "question_text": "Explain why cleaning data matters.",
  "question_type": "ESSAY",
  "points": 10,
  "model_response_guide": "A good answer mentions accuracy, consistency, missing values, and better decisions.",
  "order": 1
}
```

Legacy field mapping:

| Figma Assessment | Legacy `/api/v1/questions/` |
| --- | --- |
| `question` | `question_text` |
| `type` | `question_type` |
| `options[].text` | `options[].option_text` |
| `correct_index` / `correct_indices` | `options[].is_correct` |
| `expected_answer` | `model_response_guide` |

Legacy `/api/v1/questions/` supports only:

- `SINGLE_CHOICE` with exactly one `is_correct: true` option.
- `MULTIPLE_CHOICE` with exactly one `is_correct: true` option, retained for
  existing relational records.
- `ESSAY` with no options.

It does not support the Figma multi-answer `correct_indices` shape.

## Error Handling

Handle these common responses:

- `404` on `GET`: no assessment exists yet; initialise an empty quiz editor.
- `404` on `PUT`: the course/module/lesson id is wrong or inaccessible; refresh
  parent ids.
- `400`: keep the form data and display field errors.

Common validation mistakes:

- Sending essay questions with options.
- Sending choice questions with fewer than 2 options.
- Sending `correct_index` for `MULTIPLE_CHOICE`.
- Sending `correct_indices` for `SINGLE_CHOICE`.
- Sending top-level `explanation` on choice questions.
