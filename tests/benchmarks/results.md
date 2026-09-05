# Endpoint performance audit

65 GET endpoints, median of 5 runs against the stress
dataset.

- **Cold** — the first request after `cache.clear()`.
- **Fill** — how long after that request the Celery worker finished
  writing the key, measured with a real worker (`celery-worker-test`),
  not eager execution. This is how long an admin keeps paying the cold
  path after any write invalidates the cache. `—` means no
  worker-filled key; **never** means the key the view reads back is not
  the key the task writes.
- **Warm** — the request after the key was confirmed present.

Rows ending `?search` / `?ordering` never consult the cache at all —
those views `return super().list(...)` unconditionally, because an
arbitrary search string is not cacheable. They are measured as the
floor cost of the uncached path, not as a cache result.

This file is regenerated on every run — the analysis lives in
`FINDINGS.md`, which is not overwritten.

| Endpoint | Role | Queries cold | Queries warm | Cold ms | Fill ms | Warm ms | Verdict |
|---|---|---:|---:|---:|---:|---:|---|
| `admin/student-list?search` | admin | 7 | 7 | 292.4 | — | 291.6 | no cache |
| `admin/student-list` | admin | 7 | 2 | 256.3 | 2396 | 131.4 | cached |
| `admin/dashboard` | admin | 17 | 2 | 162.6 | — | 7.8 | cached |
| `admin/student-list?class_` | admin | 7 | 7 | 140.7 | — | 216.9 | no cache |
| `faculty/lectures` | faculty | 15 | 15 | 124.8 | — | 122.9 | no cache |
| `admin/student-list?status` | admin | 7 | 2 | 122.2 | 13771 | 138.7 | cached, marginal |
| `admin/student-list?dept` | admin | 8 | 2 | 76.9 | 3437 | 152.2 | cached, marginal |
| `student/enrollments` | student | 6 | 6 | 74.8 | — | 67.9 | no cache |
| `admin/student-list?dept+status` | admin | 8 | 2 | 71.4 | 3804 | 116.1 | cached, marginal |
| `admin/student-detail` | admin | 30 | 30 | 70.4 | — | 59.8 | no cache |
| `admin/student-list?program` | admin | 8 | 2 | 62.9 | 7404 | 85.0 | cached, marginal |
| `admin/allocation-detail` | admin | 11 | 11 | 61.7 | — | 65.7 | no cache |
| `admin/classes` | admin | 6 | 6 | 61.4 | — | 66.4 | no cache |
| `admin/enrollments?faculty` | admin | 8 | 3 | 60.0 | 352 | 22.8 | cached |
| `faculty/allocation-detail` | faculty | 14 | 14 | 55.1 | — | 52.1 | no cache |
| `admin/allocations-bulk` | admin | 7 | 4 | 51.4 | — | 24.8 | cached |
| `admin/student-list?student_class` | admin | 8 | 2 | 50.7 | 9599 | 83.8 | cached, marginal |
| `admin/faculty-list?search` | admin | 6 | 6 | 50.5 | — | 53.6 | no cache |
| `admin/faculty-list` | admin | 6 | 2 | 49.9 | 47 | 15.1 | cached |
| `faculty/assessments` | faculty | 10 | 3 | 48.4 | — | 9.6 | cached |
| `admin/semesters` | admin | 5 | 2 | 47.9 | 25 | 14.6 | cached |
| `faculty/assessment-detail` | faculty | 11 | 11 | 46.7 | — | 44.6 | no cache |
| `admin/faculty-list?dept` | admin | 7 | 2 | 45.8 | 169 | 17.0 | cached |
| `admin/faculty-list?ordering` | admin | 6 | 6 | 45.3 | — | 50.9 | no cache |
| `admin/faculty-list?dept+desig` | admin | 7 | 2 | 44.4 | 231 | 15.4 | cached |
| `admin/semesters?class` | admin | 6 | 2 | 43.0 | 126 | 15.4 | cached |
| `admin/enrollments` | admin | 7 | 7 | 40.1 | — | 39.0 | no cache |
| `admin/faculty-detail` | admin | 13 | 13 | 38.7 | — | 44.9 | no cache |
| `student/profile` | student | 13 | 13 | 38.5 | — | 34.0 | no cache |
| `admin/semester-detail` | admin | 14 | 14 | 38.4 | — | 35.6 | no cache |
| `admin/allocations?faculty` | admin | 7 | 2 | 37.3 | 1 | 10.8 | cached |
| `faculty/lecture-detail` | faculty | 9 | 9 | 35.9 | — | 37.2 | no cache |
| `student/dashboard` | student | 11 | 2 | 34.8 | — | 9.3 | cached |
| `faculty/allocations` | faculty | 7 | 2 | 33.5 | — | 7.3 | cached |
| `admin/enrollments?student` | admin | 8 | 3 | 33.2 | 1 | 13.6 | cached |
| `admin/allocations?semester` | admin | 7 | 2 | 32.0 | 22 | 10.8 | cached |
| `admin/courses?search` | admin | 5 | 5 | 29.8 | — | 31.2 | no cache |
| `admin/courses` | admin | 5 | 2 | 29.2 | 44 | 11.7 | cached |
| `admin/profile` | admin | 8 | 2 | 29.1 | — | 7.5 | cached |
| `faculty/profile` | faculty | 10 | 2 | 28.5 | — | 6.7 | cached |
| `admin/allocations?search` | admin | 6 | 6 | 28.4 | — | 26.0 | no cache |
| `admin/faculty-list?desig` | admin | 6 | 2 | 28.2 | 756 | 15.1 | cached |
| `admin/class-detail` | admin | 5 | 5 | 26.5 | — | 19.4 | no cache |
| `student/enrollment-detail` | student | 8 | 8 | 26.4 | — | 26.7 | no cache |
| `admin/allocations` | admin | 6 | 6 | 25.3 | — | 24.3 | no cache |
| `faculty/dashboard` | faculty | 5 | 2 | 24.2 | — | 7.2 | cached |
| `admin/programs?dept` | admin | 5 | 2 | 24.2 | 22 | 10.7 | cached |
| `admin/enrollment-detail` | admin | 10 | 10 | 23.9 | — | 20.5 | no cache |
| `admin/transcripts` | admin | 4 | 4 | 22.1 | — | 22.4 | no cache |
| `student/attendance-detail` | student | 7 | 7 | 21.3 | — | 18.8 | no cache |
| `admin/programs` | admin | 4 | 2 | 21.1 | 22 | 10.4 | cached |
| `student/attendance` | student | 5 | 5 | 18.6 | — | 18.4 | no cache |
| `admin/sessions` | admin | 4 | 4 | 15.9 | — | 11.8 | no cache |
| `admin/departments` | admin | 4 | 4 | 15.6 | — | 15.9 | no cache |
| `admin/requests` | admin | 3 | 3 | 15.3 | — | 10.9 | no cache |
| `admin/session-detail` | admin | 3 | 3 | 14.3 | — | 10.9 | no cache |
| `admin/department-detail` | admin | 3 | 3 | 13.5 | — | 13.0 | no cache |
| `admin/program-detail` | admin | 3 | 3 | 13.3 | — | 9.7 | no cache |
| `admin/course-detail` | admin | 3 | 3 | 10.8 | — | 9.7 | no cache |
| `student/transcripts` | student | 4 | 4 | 10.2 | — | 10.6 | no cache |
| `notifications/list` | student | 2 | 2 | 10.2 | — | 10.7 | no cache |
| `faculty/requests` | faculty | 3 | 3 | 9.8 | — | 10.3 | no cache |
| `public/current-session` | anon | 1 | 1 | 9.2 | — | 8.7 | no cache |
| `student/reviews` | student | 3 | 3 | 7.2 | — | 7.2 | no cache |
| `notifications/unread-count` | student | 2 | 2 | 5.9 | — | 5.9 | no cache |
