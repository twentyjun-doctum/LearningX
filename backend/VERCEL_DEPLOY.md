# LearningX FastAPI → Vercel 배포

목표: **프론트엔드와 백엔드가 동일한 GitHub 저장소를 사용하되, 서로 다른 Vercel 프로젝트로 배포**. 기존 Oil&Fat API나 기존 LearningX 정적 사이트 프로젝트의 설정은 변경하지 않습니다.

## 프로젝트 설정

[Vercel 새 프로젝트](https://vercel.com/new)에서 GitHub `zxmk9123-creator/LearningX`를 Import:

| 설정 | 값 |
| --- | --- |
| Project Name | `learningx-api` (기존 `learning-x` 정적 웹과 구별) |
| Root Directory | **`backend`** |
| Framework | **FastAPI** (자동 감지; Other로 강제하지 않음) |
| Install/Build Command | Override 해제 |
| Output Directory | Override 해제 |
| Production Branch | `main` |
| Functions region | `sin1` (Neon Singapore와 동일, `backend/vercel.json`) |
| Python | `3.12` (`backend/.python-version`) |

`backend/app.py`는 실제 앱 `learningx.api:app`를 재사용하는 Vercel 공식 진입점입니다. `backend/vercel.json`은 함수 실행 리전만 지정하며 FastAPI 경로를 재작성하지 않습니다.

## 서버 환경변수

Vercel → learningx-api → Settings → Environment Variables에서 Production에 등록:

| 이름 | 값/획득 방법 |
| --- | --- |
| `DATABASE_URL` | Neon `learningx-pilot` → Connect → database `learningx` → **Direct** URL 복사; 현재 백엔드가 시작 시 SQLAlchemy `create_all`을 호출하므로 파일럿 초기에는 직접 연결 사용 |
| `NIT_JWT_SECRET` | 서로 다른 강력한 랜덤 키 생성 (`openssl rand -hex 32`) |
| `NIT_REVIEW_SIGNING_KEY` | JWT 키와 **다른** 랜덤 키 (`openssl rand -hex 32`) |
| `NIT_CORS_ORIGINS` | `https://learning-x-one.vercel.app` |
| `NIT_LLM_BASE_URL` | 본인의 FreeLLMAPI 라우터 OpenAI-compatible base URL (예: `https://<router>/v1`) |
| `NIT_LLM_MODEL` | `auto` (라우터가 지원하는 모델) |
| `NIT_LLM_API_KEY` | FreeLLMAPI Keys 페이지에서 발급한 현재 유효한 통합 API 키 |
| `NIT_WORKER_SECRET` | 기존 내부 워커 HTTP 엔드포인트용 32자 이상 별도 비밀키 (Queues 비공개 구독자 자체에서는 불필요) |
| `NIT_QUEUE_ENABLED` | `1` (Vercel 배포 시 Queues 자동 발행 활성화) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Neon Object Storage 브랜치 발급 자격증명 |
| `AWS_ENDPOINT_URL_S3` | Neon 프로젝트 `learningx-pilot`의 branch S3 endpoint |
| `AWS_REGION` | `ap-southeast-1` |
| `NIT_STORAGE_BUCKET` | `learningx-private-lessons` (Private) |

**Vercel 정적 프론트엔드 환경변수로 비밀키를 보내지 마세요.** 프론트는 `/app.html` 서버 연결 모드에서 백엔드 URL과 **교수자/학생별 단기 JWT**만 메모리로 사용합니다.

`backend/app.py`가 `NIT_SERVERLESS_RUNTIME=1`을 내부에서 설정하므로 운영자가 이 변수를 직접 입력할 필요가 없습니다.

## 연결 검증 순서

배포가 READY인 후 생성된 `https://learningx-api-...vercel.app`에서:

1. `GET /health` → `{"status":"ok","service":"learningx-backend"}`
2. `GET /health/db` → `{"status":"ok","database":"connected"}` (실제 Neon SELECT 1)
3. 교수자 및 학생 계정 발급은 [backend/README.md](README.md)의 `learningx.admin` 운영 CLI 절차 사용. **로컬 관리자 작업에도 동일한 Neon 연결 주소와 JWT 서명키 필요**
4. Vercel 프론트엔드 `https://learning-x-one.vercel.app/app.html` → **서버 연결** → `learningx-api` 도메인과 JWT 입력

환경변수 변경은 새로운 배포를 해야 적용됩니다. SQLite가 실수로 사용되지 않도록 Vercel 서버 런타임에서는 `DATABASE_URL`이 없으면 서버 초기화를 거부합니다.

## 교안 100MB 직접 업로드 및 비동기 분석

현재 개발 브랜치는 기존 Vercel 4.5MB 요청 본문 한도를 우회하기 위해 **서명 URL을 이용한 직접 업로드**를 구현했습니다. 상세 계약: [UPLOAD_ARCHITECTURE.md](UPLOAD_ARCHITECTURE.md).

- **교수자:** `POST /v1/courses/{id}/uploads/initiate` → Neon S3에 브라우저 직접 `PUT` → `POST /v1/courses/{id}/uploads/{upload_id}/complete` (202/QUEUED).
- **별도 워커:** `POST /v1/internal/ingest/process`는 `NIT_WORKER_SECRET`으로 인증하고 DB 리스 기반으로 한 건씩 분석합니다. **Vercel Queues Python 구독자 코드가 구현되어 있습니다. Vercel 프로젝트 생성 및 실제 큐 등록·실동작은 아직 미검증입니다.** 작업 상태는 `GET /v1/courses/{id}/uploads/{upload_id}`에서 확인합니다.
- **교안 산출물:** 비공개 Neon Object Storage `learningx-private-lessons`에 원문, Canonical JSON, 교수자 서명 산출물을 저장하고 요청마다 SHA-256 무결성을 확인합니다.
- **PPTX:** 파서 감사는 PDF arbitration 비교가 해당하지 않음을 보고하고, 오디오/내레이션을 처리하지 않았다면 교수자에게 고지합니다. 내레이션 포함 교안의 근거 승인에는 명시적 확인이 필요합니다.
- **기존 Multipart:** Vercel의 `POST /v1/courses/{id}/materials`는 410으로 응답해 직접 업로드를 안내합니다.
- **검증 엔진:** 기존 NIT v1.6.2 소스를 `backend/nit_engine`에 이식했으며 엔진 가용성을 확인한 후 사용합니다. 실모델 학습 품질 검증은 별도입니다.

현재 구현은 **코드 및 모의 통합 테스트 단계**입니다. 실제 32.9MB PPTX·367쪽 PDF 업로드, Neon 브라우저 CORS, 대용량 처리 시간, FreeLLMAPI 실연동, Vercel Queues 실제 메시지 전달, Vercel 서버 배포 및 HTTPS E2E는 아직 통과되지 않았습니다. 운영 파일럿 전 검증 필수입니다.

서버리스 함수 콜드 스타트는 0초가 아닙니다. 기존 Oil&Fat Vercel 프로젝트 및 LearningX 프론트엔드 프로젝트와 **별도 `learningx-api` 프로젝트**를 사용합니다.

참고: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi), [Vercel Python Runtime](https://vercel.com/docs/functions/runtimes/python), [Functions Limitations](https://vercel.com/docs/functions/limitations).
