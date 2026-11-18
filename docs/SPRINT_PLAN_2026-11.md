# Question-up sprint plan

Target dates use Asia/Seoul (KST). Dates are estimates, not scheduled automation.

| Milestone | Target | Scope | Done when |
| --- | --- | --- | --- |
| [S1](https://github.com/zxmk9123-creator/LearningX/milestone/1) | Nov 18–20 | Redesign preview/release; five audit findings | Tests pass; API/worker/frontend release aligned; scope bypass regressions blocked |
| [S2](https://github.com/zxmk9123-creator/LearningX/milestone/2) | Nov 23–25 | Original PDF/PPTX and real model classroom E2E; memory/load/mobile | Remote evidence recorded; worker limits measured; unprocessed media shown accurately |
| [S3](https://github.com/zxmk9123-creator/LearningX/milestone/3) | Nov 26–Dec 2 | Student authentication, faculty identity view, durable learning history | Persistent identity; course isolation; reconnect history; migration/retention policy verified |

Execution issues: [#14](https://github.com/zxmk9123-creator/LearningX/issues/14),
[#15](https://github.com/zxmk9123-creator/LearningX/issues/15),
[#16](https://github.com/zxmk9123-creator/LearningX/issues/16).

## Release sequence

1. Inspect PR #13's existing Vercel preview (`cb4cc2c`); keep production separately tracked.
2. Add bounded API-origin selection, session-scoped anonymous tokens, pause enforcement,
   formula-safe CSV, and the 1,000-item faculty approval limit with regressions.
3. Run backend/frontend/container CI; merge the reviewed PR; verify production Vercel.
4. Align Northflank API and queue worker with the release commit; confirm startup/DB health.
5. Verify the actual upload/model/classroom path independently under S2.

Production UI and server health do not establish that real lesson/model E2E passed.
S2 needs an existing faculty invitation entered privately, provider configuration,
original lesson files and enough worker memory. The 256 MB free worker currently
has limited headroom; do not enable OCR/Vision or upgrade paid resources implicitly.

S3 must distinguish Google account sign-in from Authenticator TOTP. Student name or
student-number collection, access rules, account linkage and persistence are designed
before external auth setup. Anonymous beta participation remains explicitly separate.
