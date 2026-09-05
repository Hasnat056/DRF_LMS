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
| `admin/student-list?search` | admin | 7 | 7 | 100.0 | — | 93.1 | no cache |
| `admin/student-list` | admin | 7 | 2 | 88.3 | 1007 | 45.0 | cached |
| `admin/student-list?class_` | admin | 7 | 7 | 81.1 | — | 82.6 | no cache |
| `admin/dashboard` | admin | 17 | 2 | 51.9 | — | 2.7 | cached |
| `faculty/lectures` | faculty | 15 | 15 | 45.3 | — | 47.4 | no cache |
| `admin/student-list?status` | admin | 7 | 2 | 41.0 | 5818 | 55.1 | cached, marginal |
| `admin/student-list?dept` | admin | 8 | 2 | 30.0 | 1318 | 51.7 | cached, marginal |
| `admin/student-list?dept+status` | admin | 8 | 2 | 29.7 | 1685 | 55.4 | cached, marginal |
| `student/enrollments` | student | 6 | 6 | 26.4 | — | 25.0 | no cache |
| `faculty/allocation-detail` | faculty | 14 | 14 | 24.3 | — | 23.9 | no cache |
| `admin/student-detail` | admin | 30 | 30 | 23.6 | — | 19.0 | no cache |
| `admin/allocation-detail` | admin | 11 | 11 | 23.5 | — | 20.5 | no cache |
| `admin/enrollments?faculty` | admin | 8 | 3 | 22.5 | 150 | 6.6 | cached |
| `admin/classes` | admin | 6 | 6 | 22.5 | — | 21.7 | no cache |
| `admin/student-list?program` | admin | 8 | 2 | 21.1 | 3401 | 39.0 | cached, marginal |
| `admin/allocations-bulk` | admin | 7 | 4 | 19.6 | — | 8.3 | cached |
| `faculty/assessments` | faculty | 10 | 3 | 18.0 | — | 3.1 | cached |
| `admin/student-list?student_class` | admin | 8 | 2 | 17.8 | 4300 | 32.6 | cached, marginal |
| `admin/faculty-list` | admin | 6 | 2 | 17.3 | 22 | 5.6 | cached |
| `faculty/assessment-detail` | faculty | 11 | 11 | 17.2 | — | 13.7 | no cache |
| `admin/faculty-list?ordering` | admin | 6 | 6 | 16.7 | — | 17.5 | no cache |
| `admin/faculty-list?search` | admin | 6 | 6 | 16.3 | — | 15.1 | no cache |
| `admin/faculty-detail` | admin | 13 | 13 | 16.2 | — | 12.3 | no cache |
| `faculty/lecture-detail` | faculty | 9 | 9 | 14.6 | — | 12.6 | no cache |
| `admin/faculty-list?dept` | admin | 7 | 2 | 14.4 | 62 | 5.4 | cached |
| `admin/enrollments` | admin | 7 | 7 | 14.3 | — | 11.9 | no cache |
| `admin/semester-detail` | admin | 14 | 14 | 13.9 | — | 13.4 | no cache |
| `admin/faculty-list?dept+desig` | admin | 7 | 2 | 13.7 | 82 | 5.1 | cached |
| `admin/semesters` | admin | 5 | 2 | 13.5 | 42 | 4.4 | cached |
| `admin/semesters?class` | admin | 6 | 2 | 13.3 | 61 | 5.3 | cached |
| `admin/enrollments?student` | admin | 8 | 3 | 13.3 | 21 | 7.3 | cached |
| `admin/faculty-list?desig` | admin | 6 | 2 | 13.2 | 287 | 5.2 | cached |
| `student/profile` | student | 13 | 13 | 12.9 | — | 8.9 | no cache |
| `admin/allocations?search` | admin | 6 | 6 | 12.3 | — | 11.3 | no cache |
| `student/enrollment-detail` | student | 8 | 8 | 11.9 | — | 9.4 | no cache |
| `admin/allocations?faculty` | admin | 7 | 2 | 11.6 | 0 | 3.2 | cached |
| `faculty/allocations` | faculty | 7 | 2 | 11.3 | — | 3.0 | cached |
| `admin/allocations?semester` | admin | 7 | 2 | 11.1 | 21 | 7.5 | cached, marginal |
| `faculty/profile` | faculty | 10 | 2 | 9.9 | — | 2.5 | cached |
| `admin/courses` | admin | 5 | 2 | 9.7 | 22 | 6.0 | cached, marginal |
| `admin/programs?dept` | admin | 5 | 2 | 9.4 | 21 | 3.9 | cached |
| `student/dashboard` | student | 11 | 2 | 9.2 | — | 2.5 | cached |
| `student/attendance-detail` | student | 7 | 7 | 9.1 | — | 8.6 | no cache |
| `admin/courses?search` | admin | 5 | 5 | 9.1 | — | 9.2 | no cache |
| `admin/allocations` | admin | 6 | 6 | 8.9 | — | 8.3 | no cache |
| `admin/enrollment-detail` | admin | 10 | 10 | 8.7 | — | 7.2 | no cache |
| `admin/programs` | admin | 4 | 2 | 8.4 | 21 | 4.1 | cached, marginal |
| `student/attendance` | student | 5 | 5 | 8.4 | — | 7.5 | no cache |
| `faculty/dashboard` | faculty | 5 | 2 | 8.4 | — | 2.8 | cached |
| `student/transcripts` | student | 4 | 4 | 6.3 | — | 6.6 | no cache |
| `admin/transcripts` | admin | 4 | 4 | 6.2 | — | 6.2 | no cache |
| `admin/class-detail` | admin | 5 | 5 | 6.1 | — | 5.4 | no cache |
| `admin/sessions` | admin | 4 | 4 | 5.7 | — | 5.8 | no cache |
| `admin/course-detail` | admin | 3 | 3 | 5.4 | — | 4.6 | no cache |
| `admin/profile` | admin | 8 | 2 | 5.3 | — | 1.7 | cached, marginal |
| `admin/requests` | admin | 3 | 3 | 4.7 | — | 4.3 | no cache |
| `admin/program-detail` | admin | 3 | 3 | 4.3 | — | 4.0 | no cache |
| `student/reviews` | student | 3 | 3 | 4.2 | — | 3.9 | no cache |
| `admin/session-detail` | admin | 3 | 3 | 4.1 | — | 3.4 | no cache |
| `admin/departments` | admin | 4 | 4 | 3.8 | — | 4.0 | no cache |
| `faculty/requests` | faculty | 3 | 3 | 3.8 | — | 3.6 | no cache |
| `admin/department-detail` | admin | 3 | 3 | 3.6 | — | 3.6 | no cache |
| `notifications/list` | student | 2 | 2 | 3.4 | — | 3.6 | no cache |
| `notifications/unread-count` | student | 2 | 2 | 3.1 | — | 2.5 | no cache |
| `public/current-session` | anon | 1 | 1 | 2.0 | — | 2.3 | no cache |
