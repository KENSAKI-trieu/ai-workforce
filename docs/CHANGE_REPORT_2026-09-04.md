# Change Report - 4 September 2026

## Executive summary

This release adds a company-defined organisation structure, improves the HR AI assistant, and gives users live progress while knowledge documents are processed. The work spans 54 files (6,794 additions and 451 removals).

## Delivered changes

- **Company structure and access control:** Replaced the fixed role model with a position tree that each company can name and manage. Positions now carry permissions, reporting lines, and employee assignments. Safety checks prevent circular reporting lines, privilege escalation, and removal of the last administrator. Existing users are migrated to matching positions.
- **Organisation management screen:** Added a **Company Structure** page where authorised users can view and manage positions, permissions, and position holders. New backend APIs support this screen.
- **HR AI improvements:** The HR assistant now uses a controlled, LLM-first conversation flow with clearer streaming status updates, better leave-request handling, approval support, and permission-based access to HR data sections.
- **Knowledge document processing:** Uploads now show live progress for parsing, chunking, embedding, and indexing. Processing can resume after interruption, and chunk/embedding counters are more accurate.
- **Operational updates:** Added the required database migrations, updated service configuration, and enabled browser access for the new progress stream.

## Business impact

Companies can define their own job titles and reporting structure without losing access to existing documents. Employees receive clearer feedback while large knowledge files are being processed, and HR requests should produce more consistent, governed responses.

## Validation status

- **Backend:** 107 focused tests passed.
- **AI service:** 16 tests passed; 3 embedding tests could not run because this environment cannot connect to the configured Gemini service.
- **Frontend lint:** Not ready to pass yet. Two errors are in the new Company Structure page, where state is updated directly inside `useEffect`. There are also 40 non-blocking warnings elsewhere in the frontend.

## Recommended next steps

1. Fix the two frontend lint errors before releasing the new screen.
2. Verify Gemini credentials and network access in staging, then rerun the AI-service embedding tests.
3. Apply the two database migrations and complete a staging smoke test for position management, HR chat, and document upload progress.
