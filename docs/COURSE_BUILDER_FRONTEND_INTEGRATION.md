# Course Builder — Frontend Integration Guide

This guide maps the Course Builder Figma flow to the API. All endpoints use
the `/api/v1` base path and require `Authorization: Bearer <access-token>`
from a Course Creator or authorised Writer.

## Builder flow and fields

| Builder screen | API resource | Editable fields |
| --- | --- | --- |
| Course information | `POST /courses/`; `PUT/PATCH /courses/{course}/` | `title`, `description`, `category`, `topic`, `difficulty_level`, `learning_objectives`, `tags`, `preview_video_url`, `thumbnail_url`, `duration_hours`, `duration_minutes`, `duration_seconds` |
| Course outline / modules | `POST /courses/{course}/modules/`; `PUT/PATCH /courses/{course}/modules/{module}/` | `title`, `order`, `description`, `learning_objectives` |
| Lessons | `POST /courses/{course}/modules/{module}/lessons/`; `PUT/PATCH /courses/{course}/modules/{module}/lessons/{lesson}/` | `title`, `order`, `lesson_type`, `script`, media URLs, `learning_objectives`, `duration_minutes`, `lesson_requirement` |
| Lesson content | `/content-blocks/`, `/images/`, `/requirements/` below a lesson | Rich-text blocks, media, and ordered requirements |
| Quizzes | Assessment endpoints below | Quiz `title` and `questions` |
| Thumbnail | `PATCH /courses/{course}/` or AI thumbnail endpoints | `thumbnail_url` |

`lesson_type` is `VIDEO`, `TEXT`, or `QUIZ`. `content_type` is a deprecated
alias. A video lesson must provide `video_url` or `embedded_link`. The course,
module, lesson, and assessment writes are permitted only while the course is
`DRAFT`.

## Quiz Builder contract

Each lesson, module, and course has at most one assessment. `GET` returns
`404` until it has been saved once; `PUT` creates or replaces it. Prefer the
nested assessment already returned by `GET /courses/{course}/` for initial
builder hydration; use the focused endpoint to save or refresh one quiz.

| Figma quiz location | Endpoint |
| --- | --- |
| Lesson quiz | `GET/PUT /courses/{course}/modules/{module}/lessons/{lesson}/assessment/` |
| Module quiz | `GET/PUT /courses/{course}/modules/{module}/assessment/` |
| Final assessment | `GET/PUT /courses/{course}/final-assessment/` |

The backend returns the same shape from all three endpoints:

```ts
type ChoiceOption = { text: string; explanation: string };

type SingleChoiceQuestion = {
  type: "SINGLE_CHOICE";
  question: string;
  points: number;
  options: ChoiceOption[];
  correct_index: number;
};

type MultipleChoiceQuestion = {
  type: "MULTIPLE_CHOICE";
  question: string;
  points: number;
  options: ChoiceOption[];
  correct_indices: number[];
};

type EssayQuestion = {
  type: "ESSAY";
  question: string;
  points: number;
  expected_answer: string;
  explanation: string;
};

type Assessment = {
  id: string;
  level: "LESSON" | "MODULE" | "COURSE";
  title: string;
  questions: (SingleChoiceQuestion | MultipleChoiceQuestion | EssayQuestion)[];
  summary: {
    total_questions: number;
    total_points: number;
    single_choice_count: number;
    multiple_choice_count: number;
    essay_count: number;
  };
};
```

### Question fields and Figma behaviour

- **Single choice** is the Figma **Question choice** view. Render radio
  buttons in preview and send exactly one `correct_index`.
- **Multiple choice** is the existing frontend's additional multi-answer
  view. Render checkboxes in preview and send one or more unique
  `correct_indices`.
- **Essay** has `question`, `points`, `expected_answer`, and `explanation`.
  The expected answer is the reference response; the explanation gives grading
  guidance. Do not render or send options or correct indexes.
- Choice questions require 2–6 options. Every option requires both its answer
  `text` and its own `explanation`, exactly as shown under each option in the
  Figma. `points` is an integer of zero or more; total points are computed by
  the backend.
- When a creator changes type, clear fields that no longer apply: remove
  options and indexes for essay; clear `correct_indices` for single choice;
  clear `correct_index` for multiple choice.

```json
{
  "title": "Variables quiz",
  "questions": [
    {
      "type": "SINGLE_CHOICE",
      "question": "Which variable name is valid?",
      "points": 10,
      "options": [
        {"text": "2value", "explanation": "It starts with a digit."},
        {"text": "user_name", "explanation": "Underscores are valid."}
      ],
      "correct_index": 1
    },
    {
      "type": "MULTIPLE_CHOICE",
      "question": "Which values are collections?",
      "points": 8,
      "options": [
        {"text": "list", "explanation": "Lists are collections."},
        {"text": "tuple", "explanation": "Tuples are collections."},
        {"text": "function", "explanation": "Functions are not collections."}
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

`PUT` returns the saved questions and computed summary for the Figma **Quiz
summary** and **Preview** tabs. The final assessment may be saved incrementally,
but the course cannot be submitted until it has at least 15 questions.

## Compatibility and integration notes

- Existing assessments with `type: "MULTIPLE_CHOICE"` and one `correct_index`
  remain valid. Treat them as legacy single-choice records; new records should
  use `SINGLE_CHOICE`.
- Do not orchestrate builder assessment saves through legacy `/quizzes/` and
  `/questions/` calls. One assessment `PUT` saves the complete question list,
  which avoids the extra list/create/delete calls in the current lesson flow.
- On `400`, retain the form and render the field errors. On `404` from a
  focused `GET`, initialise an empty quiz; on `404` from `PUT`, refresh the
  parent course/module/lesson identifiers because the target is missing or not
  accessible.
- The server is authoritative for `summary`; do not persist a client-computed
  total.

## AI-created courses

After `POST /course-ai-generations/` reaches `COMPLETED`, read `course` or
`result.course_id`, load `GET /courses/{course}/`, and initialise the same
builder data above. AI generation creates module and final assessments; lesson
assessments are optional and can be added through the lesson endpoint. See
[AI_COURSE_FRONTEND_INTEGRATION.md](AI_COURSE_FRONTEND_INTEGRATION.md) for the
generation job, polling, cancellation, and retry flow.
