-- =========================================================
-- COURSE PLATFORM SCHEMA (PostgreSQL)
-- Covers: Create Course wizard, Modules/Lessons, Quiz Builder,
-- Collaborators, Category requests, Quality check.
-- Assumes a pre-existing `auth_user` / `users_user` table (Django's
-- default or a custom user model). Adjust FK target if needed.
-- =========================================================

-- ---------------------------------------------------------
-- EXTENSIONS
-- ---------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------
-- CATEGORY / TOPIC
-- ---------------------------------------------------------
CREATE TABLE category (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(150) NOT NULL UNIQUE,
    slug            VARCHAR(160) NOT NULL UNIQUE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE topic (
    id              SERIAL PRIMARY KEY,
    category_id     INTEGER NOT NULL REFERENCES category(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    slug            VARCHAR(160) NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category_id, slug)
);

-- User-submitted request when a category/topic they need isn't listed
CREATE TABLE category_request (
    id              SERIAL PRIMARY KEY,
    requested_by_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
    requested_name  VARCHAR(200) NOT NULL,
    note            TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ
);

-- ---------------------------------------------------------
-- COURSE VERSION (lookup — step: "Versioning" in the wizard)
-- ---------------------------------------------------------
CREATE TABLE course_version (
    id          SERIAL PRIMARY KEY,
    label       VARCHAR(20) NOT NULL UNIQUE,   -- e.g. 'v1.0', 'v1.1'
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------
-- COURSE
-- ---------------------------------------------------------
CREATE TABLE course (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id            INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,

    -- Step: category / topic
    category_id         INTEGER REFERENCES category(id) ON DELETE SET NULL,
    topic_id             INTEGER REFERENCES topic(id) ON DELETE SET NULL,

    -- Step: title
    title               VARCHAR(255),
    description         TEXT,

    -- Step: course information
    difficulty_level    VARCHAR(20)
                            CHECK (difficulty_level IN ('beginner', 'intermediate', 'advanced')),
    overview            TEXT,

    -- Step: versioning
    version_id          INTEGER REFERENCES course_version(id) ON DELETE SET NULL,

    -- Legal / wizard progress
    legal_agreed        BOOLEAN NOT NULL DEFAULT FALSE,
    legal_agreed_at      TIMESTAMPTZ,
    quality_checked      BOOLEAN NOT NULL DEFAULT FALSE,
    quality_checked_at   TIMESTAMPTZ,

    -- Publishing state. NOTE: actual publish (superadmin review/approval
    -- flow) is a separate system handled later — statuses here reflect
    -- what's shown on the creator dashboard (Overview cards + Status column).
    status              VARCHAR(20) NOT NULL DEFAULT 'draft'
                            CHECK (status IN (
                                'draft', 'in_review', 'needs_revision',
                                'rejected', 'approved', 'published', 'archived'
                            )),
    quality_score       SMALLINT CHECK (quality_score BETWEEN 0 AND 100),  -- shown as % bar in course table
    source_type         VARCHAR(20) NOT NULL DEFAULT 'creator_uploaded'
                            CHECK (source_type IN ('creator_uploaded', 'ai_generated')),  -- "Type" badge in Course details
    published_at        TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_course_owner ON course(owner_id);
CREATE INDEX idx_course_status ON course(status);
CREATE INDEX idx_course_category ON course(category_id);

-- ---------------------------------------------------------
-- COURSE THUMBNAIL (step: "Thumbnail" — via "Add Media" modal)
-- One active thumbnail per course; either an uploaded file or an
-- external source (Google Drive, YouTube, Dropbox, pasted link).
-- ---------------------------------------------------------
CREATE TABLE course_thumbnail (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id       UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    media_type      VARCHAR(10) NOT NULL DEFAULT 'image'
                        CHECK (media_type IN ('image', 'video')),
    source          VARCHAR(20) NOT NULL DEFAULT 'upload'
                        CHECK (source IN ('upload', 'google_drive', 'youtube', 'dropbox', 'link')),
    file            VARCHAR(500),          -- populated when source = 'upload'
    external_url    VARCHAR(1000),         -- populated when source != 'upload'
    width           INTEGER,
    height          INTEGER,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,   -- lets old thumbnails be kept/replaced
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_thumbnail_source_value CHECK (
        (source = 'upload' AND file IS NOT NULL)
        OR (source != 'upload' AND external_url IS NOT NULL)
    )
);

CREATE INDEX idx_thumbnail_course ON course_thumbnail(course_id);
-- Only one active thumbnail per course at a time
CREATE UNIQUE INDEX idx_one_active_thumbnail_per_course
    ON course_thumbnail(course_id)
    WHERE is_active = TRUE;

-- Tags (many-to-many)
CREATE TABLE tag (
    id      SERIAL PRIMARY KEY,
    name    VARCHAR(80) NOT NULL UNIQUE,
    slug    VARCHAR(90) NOT NULL UNIQUE
);

CREATE TABLE course_tag (
    course_id   UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, tag_id)
);

-- Learning objectives at the course level
CREATE TABLE course_learning_objective (
    id          SERIAL PRIMARY KEY,
    course_id   UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    text        VARCHAR(500) NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_course_objective_course ON course_learning_objective(course_id);

-- ---------------------------------------------------------
-- COLLABORATORS ("Add People" step)
-- ---------------------------------------------------------
CREATE TABLE course_collaborator (
    id              SERIAL PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    invited_email   VARCHAR(255),        -- used when the invited person has no account yet
    role            VARCHAR(20) NOT NULL DEFAULT 'editor'
                        CHECK (role IN ('owner', 'editor', 'reviewer', 'viewer')),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'accepted', 'declined', 'revoked')),
    invited_by_id   INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
    invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    responded_at    TIMESTAMPTZ,
    UNIQUE (course_id, user_id)
);

CREATE INDEX idx_collaborator_course ON course_collaborator(course_id);

-- ---------------------------------------------------------
-- WORKSPACE COLLABORATORS (account-level "Collaborators" sidebar page)
-- Distinct from course_collaborator above: this is the creator's overall
-- team roster (not scoped to one course), listing everyone they work
-- with, with a platform-wide role. course_collaborator can later assign
-- one of these people to a specific course.
-- ---------------------------------------------------------
CREATE TABLE workspace_collaborator (
    id                  SERIAL PRIMARY KEY,
    owner_id            INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,  -- the workspace/creator account
    user_id             INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,          -- null until invite is accepted
    invited_email       VARCHAR(255) NOT NULL,
    role                VARCHAR(20) NOT NULL DEFAULT 'collaborator'
                            CHECK (role IN ('admin', 'author', 'collaborator')),
    sex                 VARCHAR(10) CHECK (sex IN ('male', 'female', 'other')),
    country_of_origin  VARCHAR(100),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'active', 'removed')),
    date_added          TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at          TIMESTAMPTZ,
    UNIQUE (owner_id, invited_email)
);

CREATE INDEX idx_workspace_collaborator_owner ON workspace_collaborator(owner_id);
CREATE INDEX idx_workspace_collaborator_role ON workspace_collaborator(role);

-- ---------------------------------------------------------
-- MODULES / COURSE OUTLINE
-- ---------------------------------------------------------
CREATE TABLE module (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id   UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    title       VARCHAR(255) NOT NULL,      -- set at "Course Outline" step
    description TEXT,                       -- filled in later at "Course Modules" step
    is_locked   BOOLEAN NOT NULL DEFAULT FALSE,  -- "Lock module": prevents collaborators editing it
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_module_course ON module(course_id);
CREATE UNIQUE INDEX idx_module_course_order ON module(course_id, order_index);

CREATE TABLE module_learning_objective (
    id          SERIAL PRIMARY KEY,
    module_id   UUID NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    text        VARCHAR(500) NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_module_objective_module ON module_learning_objective(module_id);

-- ---------------------------------------------------------
-- LESSONS
-- ---------------------------------------------------------
CREATE TABLE lesson (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    module_id               UUID NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    title                   VARCHAR(255) NOT NULL,

    -- Matches the 3 "Add lesson" buttons: Video, Quiz, Text
    content_type            VARCHAR(20) NOT NULL DEFAULT 'text'
                                CHECK (content_type IN ('video', 'quiz', 'text')),

    -- "Add Media" block at the top of the lesson editor
    video_file              VARCHAR(500),   -- uploaded video/image file
    embedded_link           VARCHAR(1000),  -- pasted embed link (Vimeo, YouTube, Wistia, Typeform, etc.)
    video_script_file       VARCHAR(500),   -- uploaded subtitle/transcript file (.srt)

    estimated_duration_minutes INTEGER,     -- shown as "1 hours 25 minutes" per lesson

    order_index             INTEGER NOT NULL DEFAULT 0,
    is_published            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_lesson_module ON lesson(module_id);
CREATE UNIQUE INDEX idx_lesson_module_order ON lesson(module_id, order_index);

CREATE TABLE lesson_objective (
    id          SERIAL PRIMARY KEY,
    lesson_id   UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    text        VARCHAR(500) NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE lesson_requirement (
    id          SERIAL PRIMARY KEY,
    lesson_id   UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    text        VARCHAR(500) NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_lesson_objective_lesson ON lesson_objective(lesson_id);
CREATE INDEX idx_lesson_requirement_lesson ON lesson_requirement(lesson_id);

-- Lesson images (the "Add image" modal — supports multiple images per lesson body)
CREATE TABLE lesson_image (
    id          SERIAL PRIMARY KEY,
    lesson_id   UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    image       VARCHAR(500) NOT NULL,
    caption     VARCHAR(255),
    source_type VARCHAR(20) NOT NULL DEFAULT 'upload'
                    CHECK (source_type IN ('upload', 'google_drive', 'youtube', 'dropbox', 'link')),
    order_index INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_lesson_image_lesson ON lesson_image(lesson_id);

-- ---------------------------------------------------------
-- QUIZ BUILDER
-- ---------------------------------------------------------
CREATE TABLE quiz (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lesson_id       UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    title           VARCHAR(255),
    pass_mark       INTEGER,               -- % or points threshold to pass
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lesson_id)                     -- one quiz per lesson (per the modal design)
);

CREATE TABLE question (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    quiz_id         UUID NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
    order_index     INTEGER NOT NULL DEFAULT 0,
    question_text   TEXT NOT NULL,
    question_type   VARCHAR(20) NOT NULL DEFAULT 'multiple_choice'
                        CHECK (question_type IN ('multiple_choice', 'essay')),
    point           INTEGER NOT NULL DEFAULT 0,
    explanation     TEXT,                  -- used directly for essay-type questions
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_question_quiz ON question(quiz_id);
CREATE UNIQUE INDEX idx_question_quiz_order ON question(quiz_id, order_index);

CREATE TABLE question_option (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id     UUID NOT NULL REFERENCES question(id) ON DELETE CASCADE,
    order_index     INTEGER NOT NULL DEFAULT 0,   -- also maps to display letter A/B/C/D
    text            VARCHAR(500) NOT NULL,
    explanation     TEXT,
    is_correct      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_option_question ON question_option(question_id);
CREATE UNIQUE INDEX idx_option_question_order ON question_option(question_id, order_index);

-- Enforce exactly one correct option per multiple-choice question
-- (partial unique index; only meaningful where is_correct = true)
CREATE UNIQUE INDEX idx_one_correct_option_per_question
    ON question_option(question_id)
    WHERE is_correct = TRUE;

-- ---------------------------------------------------------
-- LESSON BODY CONTENT — block-based editor (the "General" panel with
-- Heading 1/2, Paragraph, Number, Bullet, Blockquote, Divider, Image,
-- Video, Embed, Quiz). This is where the actual lesson body content
-- ("Definition", "Types of computer", etc. seen in preview) is composed,
-- block by block, rather than a single body textarea.
-- ---------------------------------------------------------
CREATE TABLE lesson_content_block (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lesson_id       UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    order_index     INTEGER NOT NULL DEFAULT 0,
    block_type      VARCHAR(20) NOT NULL
                        CHECK (block_type IN (
                            'heading_1', 'heading_2', 'paragraph',
                            'numbered_list', 'bulleted_list', 'blockquote',
                            'divider', 'image', 'video', 'embed', 'quiz'
                        )),
    text_content    TEXT,           -- for heading/paragraph/list/blockquote blocks
    media_url       VARCHAR(500),   -- for image/video/embed blocks
    quiz_id         UUID REFERENCES quiz(id) ON DELETE SET NULL,  -- for a 'quiz' block referencing this lesson's quiz
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_content_block_lesson ON lesson_content_block(lesson_id);
CREATE UNIQUE INDEX idx_content_block_lesson_order ON lesson_content_block(lesson_id, order_index);

-- ---------------------------------------------------------
-- QUALITY CHECK (pre-submission checklist)
-- Admin-configurable: criteria are a reusable template grouped by
-- wizard section ("Course information", "Course Outline", "Version",
-- "Course Modules", "Thumbnail"...), so admins can add/remove/reorder
-- checklist items without a schema change. Each course gets its own
-- result row per criterion, generated/refreshed as the creator edits.
-- ---------------------------------------------------------
CREATE TABLE quality_check_criterion (
    id          SERIAL PRIMARY KEY,
    section     VARCHAR(100) NOT NULL,   -- e.g. 'Course information', 'Course Outline', 'Version', 'Course Modules', 'Thumbnail'
    label       VARCHAR(255) NOT NULL,   -- e.g. 'Course title', 'Learning objectives'
    order_index INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,  -- lets admin retire a criterion without deleting history
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_quality_criterion_section ON quality_check_criterion(section);

CREATE TABLE course_quality_check (
    id              SERIAL PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    criterion_id    INTEGER NOT NULL REFERENCES quality_check_criterion(id) ON DELETE CASCADE,
    is_checked      BOOLEAN NOT NULL DEFAULT FALSE,
    warning_note    VARCHAR(500),   -- e.g. "Your description does not meet the minimum requirement"
    checked_at      TIMESTAMPTZ,
    UNIQUE (course_id, criterion_id)
);

CREATE INDEX idx_course_quality_check_course ON course_quality_check(course_id);

-- ---------------------------------------------------------
-- COURSE REVIEW (admin review rounds + flagged issues)
-- Shown on the creator dashboard's "Course details" panel when status
-- is Rejected/Needs Revision — e.g. "P1 Lesson 2 - Script Length,
-- 306/500 words below minimum" with a reviewer note underneath.
-- ---------------------------------------------------------
CREATE TABLE course_review (
    id              SERIAL PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(id) ON DELETE CASCADE,
    reviewer_id     INTEGER REFERENCES auth_user(id) ON DELETE SET NULL,
    decision        VARCHAR(20) NOT NULL
                        CHECK (decision IN ('approved', 'rejected', 'needs_revision')),
    overall_note    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_course_review_course ON course_review(course_id);

-- A specific flagged issue within a review round, optionally scoped to
-- one lesson or module (e.g. "P1 Lesson 2 - Script Length")
CREATE TABLE course_review_flag (
    id              SERIAL PRIMARY KEY,
    review_id       INTEGER NOT NULL REFERENCES course_review(id) ON DELETE CASCADE,
    lesson_id       UUID REFERENCES lesson(id) ON DELETE SET NULL,
    module_id       UUID REFERENCES module(id) ON DELETE SET NULL,
    flag_type       VARCHAR(50) NOT NULL,      -- e.g. 'script_length', 'missing_media', 'quiz_incomplete'
    title           VARCHAR(255) NOT NULL,     -- e.g. "P1 Lesson 2 - Script Length"
    system_message  VARCHAR(500),              -- e.g. "306/500 words below minimum"
    reviewer_note   TEXT,                      -- e.g. "Extend the lesson script to resolve this issue"
    is_resolved     BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_review_flag_review ON course_review_flag(review_id);
CREATE INDEX idx_review_flag_lesson ON course_review_flag(lesson_id);

-- ---------------------------------------------------------
-- UPDATED_AT TRIGGER HELPER (optional but recommended)
-- ---------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_course_updated_at
    BEFORE UPDATE ON course
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_module_updated_at
    BEFORE UPDATE ON module
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_lesson_updated_at
    BEFORE UPDATE ON lesson
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_quiz_updated_at
    BEFORE UPDATE ON quiz
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
