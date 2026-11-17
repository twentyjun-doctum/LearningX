# LearningX — 배포 전 교안 업로드/분석 P0 계약

## 결정: 직접 업로드 + 별도 분석 작업

Vercel API는 4.5MB 요청 본문 제한이 있으므로 32.9MB PPTX와 대형 PDF를 수신하지 않습니다.

1. 교수자가 POST /v1/courses/{course_id}/uploads/initiate 에 파일명·크기만 전송하고 Neon Private Object Storage의 15분 유효 presigned PUT URL을 받습니다.
2. 브라우저가 원본 파일을 Neon S3에 직접 PUT 합니다. 교수자 JWT는 S3로 전송하지 않습니다.
3. POST /v1/courses/{course_id}/uploads/{upload_id}/complete 는 S3 HEAD로 바이트 수를 확인하고 DB에 QUEUED 상태만 저장합니다. 문서 파싱은 이 요청에서 실행되지 않습니다.
4. 별도 작업 실행기가 POST /v1/internal/ingest/process 를 서버 전용 NIT_WORKER_SECRET으로 호출합니다. 리스된 작업 하나를 실행하고 원본 해시를 확인하여 NIT 분석 산출물을 private S3에 영구 저장합니다.
5. 교수자는 GET /v1/courses/{course_id}/uploads/{upload_id} 로 AWAITING_UPLOAD / QUEUED / RUNNING / READY / FAILED 상태와 분석 누락 범위를 조회합니다. READY 이후에만 교수자 승인 후보를 검토할 수 있습니다.

현재 최대 파일 크기는 100MB입니다. 367페이지 PDF 등 고비용 자료의 실행 시간과 메모리 사용량을 실측하기 전에는 상한을 상향하지 않습니다.
교안 업로드·분석·승인 권한은 과목 DB 멤버십과 교수자/학생 역할 검사로 분리합니다.
기존 multipart POST /v1/courses/{id}/materials 는 Vercel에서 410으로 거부합니다.

## 필수 환경변수

DATABASE_URL: 기존 Neon learningx-pilot/learningx 연결 문자열
NIT_JWT_SECRET: 서버 JWT 서명키
NIT_REVIEW_SIGNING_KEY: 교수자 승인 서명키
NIT_CORS_ORIGINS: https://learning-x-one.vercel.app
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY: Neon branch-scoped 비공개 S3 인증
AWS_ENDPOINT_URL_S3: Neon S3 엔드포인트
AWS_REGION: ap-southeast-1
NIT_STORAGE_BUCKET: learningx-private-lessons
NIT_WORKER_SECRET: JWT/서명키와 별도인 최소 32자 비밀키
NIT_LLM_BASE_URL, NIT_LLM_MODEL, NIT_LLM_API_KEY: 별도의 FreeLLMAPI 연결 값

## PPTX 감사 및 미분석 내레이션

이전 버전의 parser-audit은 PDF 전용 parser_arbitration.jsonl을 PPTX에서도 열어 FileNotFoundError를 발생시켰습니다.
수정 후 PPTX에서는 PDF arbitration 비교가 적용되지 않음을 명시합니다.
PPTX에 포함된 audio/video와 외부 audio 링크를 탐지하여 media_coverage로 보고하지만 전사/내용 분석은 수행하지 않습니다.
교수자가 녹음 포함 PPTX에서 근거 승인할 때 acknowledge_unprocessed_media=true의 명시적 확인이 필요합니다.
PPTX에 음성이 탐지되지 않아도 별도 외부 강의 녹음의 존재 여부를 판정하는 것은 아닙니다.

## 미완료 배포 조건

- 자동 작업 디스패처(예: Vercel Queues 또는 별도 안정적 실행기)는 아직 연결되지 않았습니다. DB QUEUED 상태는 워커가 호출되지 않으면 계속 유지됩니다.
- 업로드용 Neon S3의 실제 브라우저 CORS preflight/PUT 성공을 검증해야 합니다.
- Vercel API 프로젝트는 이전 연결 권한 403으로 아직 실제 생성되지 않았습니다.
- 작업 실패 및 timeout 재시도, 대형 입력 메모리, 부적절한 ZIP/PPTX, 동시 작업을 운영 환경에서 시험해야 합니다.
- 실물 24장 PPTX 파서 감사 및 합성 오디오 시나리오는 통과했습니다. 32.9MB 집단극단화 PPTX 원문은 현재 실행 환경에 없어 미시험입니다.
- 모델 타임아웃은 파싱 비동기화만으로 해결되지 않습니다. 코칭 호출의 Timeout Fallback과 별도 품질 측정이 필요합니다.

## 테스트 범위

GitHub 테스트는 Fake S3, Mock NIT parser, 합성 PPTX를 사용한 접근제어·직접 업로드·워커·승인·복원·오디오 표시 회귀 테스트입니다.
실제 Neon Object Storage 네트워크 PUT과 FreeLLMAPI End-to-End 증명이 아닙니다.

공식 참고: https://vercel.com/docs/functions/limitations · https://vercel.com/docs/queues · https://neon.com/docs/storage/get-started
