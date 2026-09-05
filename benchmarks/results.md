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
| `admin/dashboard` | admin | 17 | 2 | 8455.1 | — | 5.4 | cached |
| `faculty/dashboard` | faculty | 1024 | 2 | 1305.8 | — | 5.4 | cached |
| `admin/allocation-detail` | admin | 386 | 386 | 749.6 | — | 802.5 | no cache |
| `admin/allocations?search` | admin | 6 | 6 | 683.7 | — | 692.7 | no cache |
| `faculty/allocation-detail` | faculty | 389 | 389 | 659.0 | — | 642.5 | no cache |
| `student/enrollment-detail` | student | 269 | 269 | 591.1 | — | 473.1 | no cache |
| `admin/enrollments` | admin | 37 | 37 | 584.9 | — | 519.1 | no cache |
| `faculty/assessment-detail` | faculty | 386 | 386 | 442.4 | — | 487.3 | no cache |
| `faculty/lecture-detail` | faculty | 384 | 384 | 413.9 | — | 444.7 | no cache |
| `student/enrollments` | student | 126 | 126 | 274.5 | — | 289.7 | no cache |
| `admin/semesters` | admin | 84 | 2 | 174.5 | 870 | 13.5 | cached |
| `admin/enrollments?faculty` | admin | 38 | 38 | 150.8 | **&gt;60s** | 134.2 | fill > 60s |
| `student/attendance` | student | 74 | 74 | 143.3 | — | 145.1 | no cache |
| `admin/student-list?status` | admin | 66 | 2 | 140.2 | 9847 | 95.9 | cached |
| `admin/faculty-list?dept` | admin | 46 | 2 | 125.3 | 42 | 14.3 | cached |
| `admin/student-list?search` | admin | 66 | 66 | 124.8 | — | 113.6 | no cache |
| `admin/faculty-list?search` | admin | 45 | 45 | 121.6 | — | 106.3 | no cache |
| `admin/enrollments?student` | admin | 38 | 38 | 116.2 | **&gt;60s** | 97.5 | fill > 60s |
| `admin/student-list?student_class` | admin | 67 | 2 | 114.7 | 6437 | 58.8 | cached |
| `admin/faculty-list?ordering` | admin | 45 | 45 | 114.6 | — | 111.7 | no cache |
| `admin/student-list` | admin | 66 | 2 | 112.4 | 1399 | 54.2 | cached |
| `admin/student-list?class_` | admin | 66 | 66 | 109.1 | — | 111.4 | no cache |
| `admin/faculty-list?dept+desig` | admin | 38 | 2 | 108.8 | 146 | 14.5 | cached |
| `admin/student-list?program` | admin | 67 | 2 | 106.4 | 5033 | 70.8 | cached |
| `admin/student-list?dept+status` | admin | 67 | 2 | 106.1 | 2562 | 59.5 | cached |
| `admin/faculty-list` | admin | 45 | 2 | 103.7 | 24 | 7.4 | cached |
| `admin/faculty-list?desig` | admin | 45 | 2 | 103.1 | 708 | 13.9 | cached |
| `admin/classes` | admin | 34 | 34 | 96.3 | — | 80.2 | no cache |
| `admin/student-list?dept` | admin | 67 | 2 | 92.5 | 1860 | 73.8 | cached |
| `admin/semesters?class` | admin | 29 | 2 | 71.4 | 1700 | 11.5 | cached |
| `admin/allocations-bulk` | admin | 7 | 4 | 60.8 | — | 26.9 | cached |
| `admin/student-detail` | admin | 30 | 30 | 55.3 | — | 50.9 | no cache |
| `admin/semester-detail` | admin | 14 | 14 | 48.2 | — | 48.9 | no cache |
| `admin/allocations?semester` | admin | 7 | 2 | 41.7 | 2151 | 11.4 | cached |
| `admin/faculty-detail` | admin | 13 | 13 | 39.3 | — | 44.2 | no cache |
| `admin/allocations?faculty` | admin | 7 | 2 | 39.2 | 3541 | 9.8 | cached |
| `faculty/lectures` | faculty | 14 | 14 | 33.4 | — | 25.8 | no cache |
| `admin/allocations` | admin | 6 | 6 | 32.4 | — | 31.7 | no cache |
| `faculty/allocations` | faculty | 7 | 2 | 30.5 | — | 8.8 | cached |
| `student/attendance-detail` | student | 13 | 13 | 28.1 | — | 26.4 | no cache |
| `student/profile` | student | 13 | 13 | 26.8 | — | 25.5 | no cache |
| `faculty/assessments` | faculty | 9 | 3 | 25.6 | — | 9.0 | cached |
| `admin/profile` | admin | 8 | 2 | 25.5 | — | 7.5 | cached |
| `admin/enrollment-detail` | admin | 10 | 10 | 23.3 | — | 24.6 | no cache |
| `student/dashboard` | student | 11 | 2 | 22.0 | — | 5.6 | cached |
| `faculty/profile` | faculty | 10 | 2 | 21.1 | — | 6.6 | cached |
| `admin/courses?search` | admin | 5 | 5 | 19.6 | — | 22.0 | no cache |
| `admin/courses` | admin | 5 | 2 | 17.3 | 43 | 9.2 | cached |
| `admin/requests` | admin | 3 | 3 | 16.6 | — | 15.7 | no cache |
| `admin/programs?dept` | admin | 5 | 2 | 15.9 | 21 | 6.8 | cached |
| `admin/class-detail` | admin | 6 | 6 | 15.8 | — | 15.4 | no cache |
| `admin/programs` | admin | 4 | 2 | 15.3 | 21 | 6.2 | cached |
| `admin/transcripts` | admin | 4 | 4 | 14.9 | — | 14.6 | no cache |
| `student/transcripts` | student | 4 | 4 | 12.4 | — | 13.9 | no cache |
| `admin/sessions` | admin | 4 | 4 | 10.4 | — | 10.8 | no cache |
| `faculty/requests` | faculty | 3 | 3 | 9.7 | — | 10.7 | no cache |
| `admin/departments` | admin | 4 | 4 | 9.6 | — | 8.6 | no cache |
| `student/reviews` | student | 3 | 3 | 9.3 | — | 8.9 | no cache |
| `admin/course-detail` | admin | 3 | 3 | 8.0 | — | 7.3 | no cache |
| `admin/session-detail` | admin | 3 | 3 | 7.9 | — | 8.3 | no cache |
| `admin/department-detail` | admin | 3 | 3 | 7.4 | — | 8.4 | no cache |
| `admin/program-detail` | admin | 3 | 3 | 7.3 | — | 7.4 | no cache |
| `notifications/list` | student | 2 | 2 | 7.2 | — | 6.9 | no cache |
| `public/current-session` | anon | 1 | 1 | 6.6 | — | 7.3 | no cache |
| `notifications/unread-count` | student | 2 | 2 | 6.2 | — | 6.4 | no cache |
