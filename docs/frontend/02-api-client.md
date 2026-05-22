# Frontend 02 — `api/`

Client axios + moduli che mappano gli endpoint backend.

---

## `src/api/client.ts`

**Scopo**: istanza axios condivisa con cookie auto-inviati e refresh
trasparente.

### Esporta

- `apiClient: AxiosInstance`.

### Configurazione

```ts
axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
  timeout: 20_000,
});
```

### Interceptor di refresh

`apiClient.interceptors.response.use(success, async error => …)`:

1. Se `error.response.status === 401`, `cfg !== undefined`,
   `cfg._retry !== true`, e l'URL non contiene `/auth/login` né
   `/auth/refresh`:
2. Marca `cfg._retry = true`.
3. Se non c'è già un refresh in flight, lo avvia: `apiClient.post("/auth/refresh")`.
4. Awaita la promise condivisa (deduplicazione).
5. Ri-esegue `apiClient(cfg)` (la richiesta originale).
6. Se il refresh fallisce, ribalta l'errore.

Variabile module-scope `refreshing: Promise<void> | null` evita refresh
concorrenti.

> Eventuali nuovi cookie sono settati direttamente dal backend
> (Set-Cookie). axios continua a inoltrare automaticamente.

---

## `src/api/types.ts`

DTO TypeScript che rispecchiano gli output Pydantic.

- `UUID = string`.
- `UserOut`, `MeOrganization`, `MeOut`.
- `OrganizationOut`.
- `Page<T>`, `PageMeta`.
- `MembershipOut`.
- `InvitationCreateResponse`, `InvitationPreview`.
- `SlideTemplateOut`, `PdfTemplateOut`.
- `AvatarOut` (senza più `audio_text`), `AvatarClipOut`,
  `AvatarClipPromptOut`, `AvatarVoiceScriptOut` `{ language_code, text,
  created_at, updated_at }`, `AvatarClipStatus`,
  `AvatarClipsAggregateStatus`.
  - `AvatarOut` include i 3 campi `musetalk_extra_margin`,
    `musetalk_left_cheek_width`, `musetalk_right_cheek_width` (`number`):
    parametri MuseTalk per-avatar usati dal «Video con Avatar» delle
    lezioni.
- `AvatarMusetalkParamsUpdate`: body del PATCH dei parametri MuseTalk —
  i 3 campi `musetalk_*` come `number` obbligatori.
- `PermissionOverrideEntry`.
- `OrganizationCourseSettingsOut`: `{ id, organization_id,
  modules_per_cfu, lessons_per_module, lesson_duration_minutes,
  assessment_lesson_enabled, multiple_choice_questions_count,
  open_questions_count, created_at, updated_at }`.

> Il keep-in-sync con il backend è manuale; un futuro `openapi-typescript`
> generato dal `openapi.json` automatizzerebbe questo step.

---

## `src/api/auth.ts`

- `authApi.login(email, password) -> Promise<void>`: `POST /auth/login`.
- `authApi.logout() -> Promise<void>`: `POST /auth/logout`.
- `authApi.me() -> Promise<MeOut>`: `GET /auth/me`.

---

## `src/api/organizations.ts`

### `OrganizationFormFields`

Tipo che riflette i campi del form (tutti optional tranne `name`+`email`).

### `appendOrg(form, data)` (helper privato)

Aggiunge tutti i campi non-vuoti a un `FormData`.

### `organizationsApi`

- `list(params) -> Promise<Page<OrganizationOut>>`: GET con
  `page/page_size/q`.
- `get(id) -> Promise<OrganizationOut>`.
- `create(data, logo) -> Promise<OrganizationOut>`: multipart, allega
  `logo` se presente. POST `/admin/organizations`.
- `update(id, data, options: {logo?, remove_logo?}) -> Promise<OrganizationOut>`:
  multipart PUT.
- `remove(id) -> Promise<void>`: DELETE.
- `enrollUser(orgId, userId, roleCode) -> Promise<void>`:
  POST `/admin/organizations/:id/memberships`.

---

## `src/api/users.ts`

### `UserCreateFields`

`{ email, full_name, password, is_platform_admin? }`.

### `usersApi`

- `list(params) -> Promise<Page<UserOut>>`.
- `create(data) -> Promise<UserOut>`.
- `update(id, partial) -> Promise<UserOut>`.

---

## `src/api/memberships.ts`

`membershipsApi`:

- `list(orgId)`: GET `/orgs/:orgId/members` → `MembershipOut[]`.
- `changeRole(orgId, userId, roleCode)`: PUT.
- `remove(orgId, userId)`: DELETE.
- `getMemberPermissions(orgId, userId)`: GET → `{membership_id, overrides}`.
- `setMemberPermissions(orgId, userId, overrides)`: PUT.
- `getRolePermissions(orgId, roleCode)`: GET → `{role_code, defaults, overrides}`.
- `setRolePermissions(orgId, roleCode, overrides)`: PUT.
- `transferCreator(orgId, targetUserId)`: POST `/orgs/:id/transfer-creator`.

---

## `src/api/invitations.ts`

`invitationsApi`:

- `create(orgId, email, roleCode) -> Promise<InvitationCreateResponse>`.
- `preview(token) -> Promise<InvitationPreview>`.
- `accept(token, payload: {full_name?, password?}) -> Promise<void>`.

---

## `src/api/permissions.ts`

`permissionsApi`:

- `catalog()`: GET `/admin/permissions/permissions` → catalogo (tutti i
  codici + ruoli).
- `getRoleDefaults(roleCode)`: GET `/admin/permissions/role-defaults?role_code=`.
- `setRoleDefaults(roleCode, permissions)`: PUT.

Re-export del tipo `PermissionOverrideEntry` da `types.ts`.

---

## `src/api/slideTemplates.ts`

### `SlideTemplateFields`

Riflette `SlideTemplateBase`.

### `SlideTemplateFiles`

Files opzionali (`background`, `logo_left`, `logo_right`) e flag
`remove_*`.

### `buildForm(fields, files)` (helper)

Costruisce un `FormData` con campi + file + flag.

### `slideTemplatesApi`

- `list(orgId)`: GET → `SlideTemplateOut[]`.
- `get(orgId, id)`: GET.
- `create(orgId, fields, files)`: POST multipart.
- `update(orgId, id, fields, files)`: PUT multipart.
- `remove(orgId, id)`: DELETE.

---

## `src/api/pdfTemplates.ts`

Stessa struttura ma con campi PDF (`page_size`, `header_height_mm`, ecc.).

---

## `src/api/avatars.ts`

`myAvatarApi` — opera sull'utente corrente (1:1, no `orgId`).

`MyAvatarUpsertFields` riflette il body multipart accettato dal backend:
non include più `audio_text` (rimosso).

```ts
myAvatarApi.get(): Promise<AvatarOut | null>
// GET /me/avatar

myAvatarApi.upsert(payload: {
  image?: File | null;
  audio?: File | null;
  audio_lang?: string;
}): Promise<AvatarOut>
// PUT /me/avatar (multipart)
// `image`, quando presente, è il JPEG quadrato 1024x1024 prodotto da
// FormAvatarImageInput dopo il crop 1:1.

myAvatarApi.remove(): Promise<void>
// DELETE /me/avatar

myAvatarApi.regenerateClips(): Promise<AvatarOut>
// POST /me/avatar/clips/regenerate

myAvatarApi.updateMusetalkParams(
  params: AvatarMusetalkParamsUpdate,
): Promise<AvatarOut>
// PATCH /me/avatar/musetalk-params
// Aggiorna i 3 parametri MuseTalk per-avatar (extra margin + larghezza
// guance sx/dx) usati dal lip-sync del «Video con Avatar» delle lezioni.

myAvatarApi.getVoiceScript(lang?: string): Promise<AvatarVoiceScriptOut | null>
// GET /me/avatar/voice-script?lang=...
// Risolve il testo da leggere durante la registrazione, con fallback
// lato server (lingua richiesta -> default piattaforma -> qualsiasi
// script disponibile -> null).
```

---

## `src/api/avatarConfig.ts`

`avatarConfigApi` — admin di piattaforma. Gestisce i prompt EN passati a
MiniMax.

```ts
avatarConfigApi.list(): Promise<AvatarClipPromptOut[]>
// GET /admin/avatar-config/prompts

avatarConfigApi.create(data: {
  prompt: string;
  label_it: string;
  is_active?: boolean;
}): Promise<AvatarClipPromptOut>
// POST /admin/avatar-config/prompts

avatarConfigApi.update(id: UUID, data: {
  prompt?: string;
  label_it?: string;
  is_active?: boolean;
}): Promise<AvatarClipPromptOut>
// PUT /admin/avatar-config/prompts/:id

avatarConfigApi.remove(id: UUID): Promise<void>
// DELETE /admin/avatar-config/prompts/:id

avatarConfigApi.reorder(orderedIds: UUID[]): Promise<AvatarClipPromptOut[]>
// PUT /admin/avatar-config/prompts/reorder

avatarConfigApi.listVoiceScripts(): Promise<AvatarVoiceScriptOut[]>
// GET /admin/avatar-config/voice-scripts

avatarConfigApi.upsertVoiceScript(
  lang: string, text: string,
): Promise<AvatarVoiceScriptOut>
// PUT /admin/avatar-config/voice-scripts/:lang

avatarConfigApi.deleteVoiceScript(lang: string): Promise<void>
// DELETE /admin/avatar-config/voice-scripts/:lang
```

---

## `src/api/pdfTemplates.ts` — note

`PdfTemplateFields` include ora `background_opacity_pct: number`
(0..100, default 15). `PdfTemplateOut` espone lo stesso campo.

---

## `src/api/courseSettings.ts`

### `OrganizationCourseSettingsInput`

Body PUT con i 6 campi business:

```ts
interface OrganizationCourseSettingsInput {
  modules_per_cfu: number;            // >= 1
  lessons_per_module: number;         // >= 1
  lesson_duration_minutes: number;    // >= 1
  assessment_lesson_enabled: boolean;
  multiple_choice_questions_count: number;  // >= 0
  open_questions_count: number;             // >= 0
}
```

### `courseSettingsApi`

```ts
courseSettingsApi.get(orgId: UUID): Promise<OrganizationCourseSettingsOut>
// GET /orgs/:orgId/course-settings (idempotente: il backend crea
// lazy la riga con i default se assente).

courseSettingsApi.update(
  orgId: UUID,
  payload: OrganizationCourseSettingsInput,
): Promise<OrganizationCourseSettingsOut>
// PUT /orgs/:orgId/course-settings
```

Permission gating richiesto lato server: `course_config:manage`.

---

## `src/api/i18n.ts`

`LanguageOut` include il nuovo campo `untranslated_count: number`
(numero di chiavi mancanti o vuote rispetto alla lingua di default).

### `AutoTranslateResponse`

```ts
interface AutoTranslateResponse {
  code: string;
  requested: number;
  translated: number;
  skipped: number;
  errors: string[];
}
```

### `i18nApi.autoTranslate`

```ts
i18nApi.autoTranslate(code: string): Promise<AutoTranslateResponse>
// POST /admin/i18n/languages/:code/auto-translate
```

Innesca il completamento via OpenAI delle chiavi mancanti per la
lingua target. Se `OPENAI_API_KEY` non è configurata, il backend
risponde `422 openai_not_configured`.

---

## `src/api/courses.ts`

API del dominio Corsi. Documentata in dettaglio in
[Courses 05 — API reference](../courses/05-api-reference.md). Sintesi
dei namespace esposti:

```ts
coursesApi.{
  list, create, get, update, updateAssignee, delete: del,
  setup: { confirmDidactic, unlock },
  documents: { upload, list, get, reprocess, delete },
  architecture: { generate, approve },
  modules: { create, update, delete, reorder, generateLessons },
  lessons: { create, update, delete, reorder },
  lessonsStructure, lessonContent, lessonSlides, lessonSpeech,
  glossary, lessonPdf, lessonSlidesPdf, lessonSpeechPdf, lessonAssets,
  lessonVideo, lessonAvatarVideo,
}
```

**Override timeout**: `coursesApi.modules.generateLessons` passa
`{ timeout: 300_000 }` al request perché la chiamata sync attende
OpenAI ~20-30s e il default `apiClient.timeout = 20_000` sarebbe
troppo basso.

**Helper di tipo**: `CourseOut`, `CourseDocumentOut`, `CourseModuleOut`,
`CourseLessonOut`, `RecommendedBibliographyItem`, `DocumentSummaryOut`.

### Verifica delle competenze — tipi e namespace

Quando `CourseLessonOut.is_assessment` è `true`, il `content_raw` della
lezione è un `LessonAssessmentRaw` (non un `LessonContentRaw`). Tipi
correlati esposti da `courses.ts`:

- `AssessmentMCOption` `{ option_id, text }`.
- `AssessmentMCQuestion` `{ question_id, text, options, correct_option_id }`.
- `AssessmentOpenQuestion` `{ question_id, text, expected_answer }`.
- `LessonAssessmentRaw` `{ lesson_id, lesson_title, is_assessment: true,
  multiple_choice_questions, open_questions }`.
- `LessonAssessmentUpdateInput` — liste opzionali per l'editing manuale.
- `isAssessmentRaw(raw)` — type-guard che narrowa
  `LessonContentRaw | LessonAssessmentRaw` su `LessonAssessmentRaw`.

L'editing manuale della verifica passa per
`coursesApi.lessonContent.updateAssessment(orgId, courseId, lessonId,
payload: LessonAssessmentUpdateInput)` → `PATCH
/{course}/lessons/{lesson}/assessment`.

### `coursesApi.lessonVideo` — Video MP4 della lezione (Fase 6)

Namespace che mappa gli endpoint del video MP4 della lezione (vedi
[Courses 12 — Lesson video](../courses/12-lesson-video.md)). 6 metodi:

```ts
coursesApi.lessonVideo.generateLesson(orgId, courseId, lessonId)
// POST /{course}/lessons/{lesson}/video/generate -> LessonVideoStatusOut

coursesApi.lessonVideo.generateBatch(orgId, courseId)
// POST /{course}/lessons-video/generate-batch -> LessonVideoBatchOut

coursesApi.lessonVideo.cancelLesson(orgId, courseId, lessonId)
// POST /{course}/lessons/{lesson}/video/cancel -> LessonVideoStatusOut

coursesApi.lessonVideo.cancelBatch(orgId, courseId)
// POST /{course}/lessons-video/cancel-batch -> LessonVideoBatchOut

coursesApi.lessonVideo.getLessonStatus(orgId, courseId, lessonId)
// GET /{course}/lessons/{lesson}/video/status -> LessonVideoStatusOut

coursesApi.lessonVideo.getCourseStatus(orgId, courseId)
// GET /{course}/lessons-video/status -> LessonVideoBatchOut
```

Gli endpoint di status sono polling-friendly (il FE rinfresca ogni 2 s
mentre c'è almeno un job in flight, vedi
[08 — Hooks](08-hooks.md)). `generateLesson`/`generateBatch` falliscono
con un `code` specifico (`speech_not_approved`, `slides_not_approved`,
`voice_sample_missing`, ...) quando le pre-condizioni mancano.

Tipi associati:

- `LessonVideoStatus` = `empty | pending | processing | ready | failed |
  cancelled`.
- `LessonVideoPhase` = `preparing | tts | rendering_slides | encoding |
  null`.
- `LessonVideoTokens` — telemetria della run (`audio_duration_s`,
  `video_duration_s`, `encode_duration_ms`, `tts_duration_ms`, `device`,
  `model_xtts`, `num_segments`, `num_slides`, `file_size_bytes`).
- `LessonVideoStatusOut` — `{ lesson_id, lesson_code, status, progress,
  progress_phase, video_url, error, attempts, generated_at, tokens,
  is_stale, speech_approved, slides_approved, voice_sample_available }`.
- `LessonVideoBatchOut` — `{ items, total, ready_count,
  processing_count, pending_count, failed_count, eligible_count,
  aggregate_progress }`.

**`XTTS_SUPPORTED_LANGUAGES`**: array `const` delle 16 lingue supportate
da XTTS-v2 (`it, en, es, fr, de, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja,
hu, ko`). Tipo derivato `XttsLanguage`. La helper `isXttsLanguage(code)`
è un type-guard che tollera varianti (`zh-*`, codici con suffisso
regionale): filtra il dropdown della lingua TTS nel tab Video.

### `coursesApi.lessonAvatarVideo` — Video con Avatar (Fase 6b)

Namespace che mappa gli endpoint del «Video con Avatar» (lip-sync
MuseTalk sovrapposto al video MP4 della lezione; vedi
[Courses 13 — Avatar video](../courses/13-avatar-video.md)). Stessi 6
metodi del namespace `lessonVideo`, su path `avatar-video` /
`lessons-avatar-video`:

```ts
coursesApi.lessonAvatarVideo.generateLesson(orgId, courseId, lessonId)
// POST /{course}/lessons/{lesson}/avatar-video/generate
//   -> LessonAvatarVideoStatusOut

coursesApi.lessonAvatarVideo.generateBatch(orgId, courseId)
// POST /{course}/lessons-avatar-video/generate-batch
//   -> LessonAvatarVideoBatchOut

coursesApi.lessonAvatarVideo.cancelLesson(orgId, courseId, lessonId)
// POST /{course}/lessons/{lesson}/avatar-video/cancel
//   -> LessonAvatarVideoStatusOut

coursesApi.lessonAvatarVideo.cancelBatch(orgId, courseId)
// POST /{course}/lessons-avatar-video/cancel-batch
//   -> LessonAvatarVideoBatchOut

coursesApi.lessonAvatarVideo.getLessonStatus(orgId, courseId, lessonId)
// GET /{course}/lessons/{lesson}/avatar-video/status
//   -> LessonAvatarVideoStatusOut

coursesApi.lessonAvatarVideo.getCourseStatus(orgId, courseId)
// GET /{course}/lessons-avatar-video/status
//   -> LessonAvatarVideoBatchOut
```

`generateLesson`/`generateBatch` falliscono con un `code`
(`lesson_video_not_ready`, `avatar_clips_not_ready`, ...) quando le
pre-condizioni mancano.

Tipi associati:

- `LessonAvatarVideoStatus` = `empty | pending | processing | ready |
  failed | cancelled`.
- `LessonAvatarVideoPhase` = `preparing | lipsync | overlay | null`.
- `LessonAvatarVideoTokens` — telemetria (`audio_duration_s`,
  `lipsync_duration_s`, `overlay_duration_ms`, `total_duration_s`,
  `runpod_job_id`, `num_ready_clips`, `overlay_scale`,
  `file_size_bytes`).
- `LessonAvatarVideoStatusOut` — `{ lesson_id, lesson_code, status,
  progress, progress_phase, video_url, error, attempts, generated_at,
  tokens, is_stale, lesson_video_ready, avatar_clips_ready }`.
- `LessonAvatarVideoBatchOut` — come `LessonVideoBatchOut` più il campo
  `avatar_clips_ready: boolean` (eleggibilità globale dell'avatar
  dell'assegnatario).

> Per l'override TTS per-corso, `CourseOut.video_language_code`
> (nullable) e `CourseUpdateInput.video_language_code` (passare `""` per
> resettare a `null`) sono già esposti dai DTO di `courses.ts`.
