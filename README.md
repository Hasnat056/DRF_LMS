# NexusAPI

A production-ready REST API backend for university academic management. Built with Django REST Framework, NexusAPI powers the complete academic lifecycle — from student enrollment and course allocation to assessment management, result calculation, and transcript generation.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Features](#features)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [API Modules](#api-modules)
- [Authentication](#authentication)
- [Caching Strategy](#caching-strategy)
- [Task Queue](#task-queue)
- [File Storage](#file-storage)
- [Testing](#testing)
- [Load Testing](#load-testing)
- [Contributing](#contributing)

---

## Overview

NexusAPI is the backend service for a university Learning Management System. It exposes a RESTful API consumed by web and mobile frontends, handling three distinct user roles — Admin, Faculty, and Student — each with their own permission boundaries and workflow.

The system manages the complete semester lifecycle:

```
Class Created → Semesters Generated → Activation Deadline Set
      ↓
Semester Activates (via Celery task) → Allocations Open
      ↓
Faculty Assigned → Students Enrolled → Assessments Created
      ↓
Results Calculated → Transcripts Generated → Semester Closed
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Client (HTTP)                     │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│              Django REST Framework                   │
│         JWT Authentication + Role Permissions        │
├──────────────┬──────────────┬───────────────────────┤
│ AdminModule  │ FacultyModule│    StudentModule       │
├──────────────┴──────────────┴───────────────────────┤
│                   Models (MySQL)                     │
├─────────────────────────────────────────────────────┤
│            Redis Cache + Celery Tasks                │
├─────────────────────────────────────────────────────┤
│              Cloudinary (File Storage)               │
└─────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Django 5.x + Django REST Framework |
| Database | MySQL 8.0 |
| Cache | Redis + django-redis |
| Task Queue | Celery + Redis broker |
| Authentication | JWT (SimpleJWT) |
| File Storage | Cloudinary |
| API Documentation | drf-spectacular (OpenAPI 3.0) |
| Containerization | Docker + Docker Compose |
| Testing | pytest + pytest-django + pytest-cov |
| Load Testing | Locust |

---

## Features

### Admin Module
- Faculty and student management with bulk CSV import
- Department, program, course, and class management
- Semester lifecycle management with automated state transitions
- Course allocation and enrollment management
- Result and transcript management
- Change request workflow with email confirmation
- Role-based dashboard with aggregated statistics

### Faculty Module
- Personal dashboard with allocation statistics
- Course allocation management
- Assessment creation and grading (Quiz, Assignment, Midterm, Final, etc.)
- Lecture recording with automatic attendance generation
- Student submission management
- Result calculation request workflow (GPA computed via absolute or bell-curve grading)

### Student Module
- Personal dashboard
- Enrollment and course history
- Assessment results and feedback
- Attendance records
- Transcript access

### System Features
- JWT authentication with refresh token support
- Multi-layer Redis caching with per-entity cache keys
- Celery-powered async tasks for semester activation, closing, and cache refresh
- GPA calculation supporting both absolute grading and bell-curve (for 20+ students)
- Automated email notifications for change request confirmations
- OpenAPI 3.0 documentation via drf-spectacular

---

## Project Structure

```
NexusAPI/
├── NexusAPI/                   # Django project config
│   ├── settings.py
│   ├── urls.py
│   ├── celery.py
│   ├── wsgi.py
│   └── asgi.py
│
├── Models/                     # Shared data models
│   └── models.py               # All Django models
│
├── AdminModule/                # Admin API
│   ├── views.py
│   ├── serializers.py
│   ├── permissions.py
│   ├── mixins.py
│   ├── tasks.py
│   ├── urls.py
│   └── tests/
│       ├── test_views.py
│       ├── test_serializers.py
│       ├── test_api.py
│       ├── test_tasks.py
│       └── test_cache.py
│
├── FacultyModule/              # Faculty API
│   ├── views.py
│   ├── serializers.py
│   ├── permissions.py
│   ├── mixins.py
│   ├── urls.py
│   └── tests/
│       ├── test_serializer.py
│       └── test_views.py
│
├── StudentModule/              # Student API
│   ├── views.py
│   ├── serializers.py
│   ├── permissions.py
│   └── urls.py
│
├── Compilers/                  # Code execution microservices
│   ├── python_compiler/
│   ├── c_compiler/
│   └── java_compiler/
│
├── conftest.py                 # Shared pytest fixtures
├── locustfile.py               # Load testing scenarios
├── docker-compose.yaml
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── setup.cfg
├── .env.example
└── README.md
```

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Git

### Installation

**1. Clone the repository:**
```bash
git clone git@github.com:yourusername/NexusAPI.git
cd NexusAPI
```

**2. Create your environment file:**
```bash
cp .env.example .env
```
Fill in your values — see [Environment Variables](#environment-variables).

**3. Build and start all services:**
```bash
docker compose up --build
```

**4. Run database migrations:**
```bash
docker compose exec backend python manage.py migrate
```

**5. Create a superuser:**
```bash
docker compose exec backend python manage.py createsuperuser
```

**6. Access the API:**

| Service | URL |
|---------|-----|
| API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/api/schema/swagger-ui/ |
| API Docs (ReDoc) | http://localhost:8000/api/schema/redoc/ |
| Redis Insight | http://localhost:8001 |

---

## Environment Variables

Copy `.env.example` to `.env` and configure:

```env
# Django
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DB_NAME=LMS
DB_USER=your-db-user
DB_PASSWORD=your-db-password
DB_HOST=database
DB_PORT=3306
MYSQL_ROOT_PASSWORD=your-root-password

# Redis
REDIS_URL=redis://redis-server:6379/0
CELERY_BROKER_URL=redis://redis-server:6379/0
CELERY_RESULT_BACKEND=redis://redis-server:6379/1

# JWT
JWT_ACCESS_TOKEN_MINUTES=60
JWT_REFRESH_TOKEN_DAYS=7

# Cloudinary
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# Email (Gmail SMTP)
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
```

> Never commit `.env` to version control. Only `.env.example` should be committed.

---

## API Modules

### Base URL
```
http://localhost:8000/api/
```

### Admin Module — `/api/admin/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/` | Admin dashboard statistics |
| GET/POST | `/faculty/` | List and create faculty |
| GET/PUT/PATCH | `/faculty/<id>/` | Retrieve and update faculty |
| GET/POST | `/students/` | List and create students |
| GET/PUT/PATCH | `/students/<id>/` | Retrieve and update student |
| GET | `/departments/` | List departments |
| GET/POST | `/programs/` | List and create programs |
| GET/POST | `/courses/` | List and create courses |
| GET | `/semesters/` | List semesters |
| GET/PATCH | `/semesters/<id>/` | Retrieve and update semester |
| GET/POST | `/classes/` | List and create classes |
| GET/PATCH | `/classes/<id>/` | Retrieve and update class |
| GET/POST | `/allocations/` | List and create allocations |
| GET/PUT/DELETE | `/allocations/<id>/` | Retrieve, update, delete allocation |
| GET/POST | `/enrollments/` | List and create enrollments |
| GET/PUT/DELETE | `/enrollments/<id>/` | Retrieve, update, delete enrollment |
| POST | `/bulk/` | Bulk create faculty or students via CSV |
| POST | `/transcripts/bulk/<semester_id>/` | Bulk generate transcripts |

### Faculty Module — `/api/faculty/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/` | Faculty dashboard |
| GET/PUT | `/profile/` | View and update profile |
| GET | `/allocations/` | Own course allocations |
| GET/PATCH | `/allocations/<id>/` | Allocation detail |
| GET | `/allocations/<id>/calculate-result/` | Request result calculation |
| GET/POST | `/allocations/<id>/assessments/` | List and create assessments |
| GET/PUT/DELETE | `/allocations/<id>/assessments/<id>/` | Assessment detail |
| GET/POST | `/allocations/<id>/lectures/` | List and create lectures |
| GET/PUT/DELETE | `/allocations/<id>/lectures/<id>/` | Lecture detail with attendance |
| GET | `/requests/` | Own change requests |
| PATCH | `/requests/<id>/` | Apply confirmed change request |

### Student Module — `/api/student/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dashboard/` | Student dashboard |
| GET/PUT | `/profile/` | View and update profile |
| GET | `/enrollments/` | Own enrollments |
| GET | `/enrollments/<id>/` | Enrollment detail with results |
| GET | `/attendance/` | Attendance records |
| GET | `/transcripts/` | Semester transcripts |

---

## Authentication

NexusAPI uses JWT authentication via `djangorestframework-simplejwt`.

**Obtain tokens:**
```http
POST /api/token/
Content-Type: application/json

{
    "username": "user@example.com",
    "password": "password"
}
```

**Response:**
```json
{
    "access": "eyJ...",
    "refresh": "eyJ..."
}
```

**Use access token:**
```http
GET /api/admin/dashboard/
Authorization: Bearer eyJ...
```

**Refresh access token:**
```http
POST /api/token/refresh/
Content-Type: application/json

{
    "refresh": "eyJ..."
}
```

**Token lifetimes** (configurable via `.env`):
- Access token: 60 minutes
- Refresh token: 7 days

---

## Caching Strategy

NexusAPI uses Redis for multi-layer caching. Cache is populated by Celery tasks and invalidated on every write operation.

| Cache Key Pattern | Data | TTL |
|------------------|------|-----|
| `admin:faculty_list` | All faculty | 5 min |
| `admin:faculty:department:{id}` | Faculty by department | 5 min |
| `admin:student_list` | All students | 5 min |
| `admin:students:program:{id}` | Students by program | 5 min |
| `admin:semesters_list` | All semesters | 5 min |
| `admin:allocations:semester:{id}` | Allocations by semester | 5 min |
| `admin:enrollments:student:{id}` | Enrollments by student | 5 min |
| `admin:dashboard:{username}` | Dashboard data | 5 min |
| `faculty:dashboard:{username}` | Faculty dashboard | 5 min |
| `faculty:{username}:allocations` | Faculty allocations | 5 min |

Search and ordering queries always bypass cache and hit the database directly.

---

## Task Queue

Celery handles all async operations with Redis as the broker.

| Task | Trigger | Action |
|------|---------|--------|
| `semester_activation_task` | `activation_deadline` reached | Inactive → Active, cascade allocations and enrollments |
| `semester_closing_task` | `closing_deadline` reached | Active → Completed, cascade all |
| `cache_faculty_data_task` | Faculty create/update | Rebuild faculty cache keys |
| `cache_student_data_task` | Student create/update | Rebuild student cache keys |
| `cache_semester_data_task` | Semester update | Rebuild semester cache keys |
| `cache_courseAllocation_data_task` | Allocation create/update | Rebuild allocation cache keys |
| `cache_enrollment_data_task` | Enrollment create/update | Rebuild enrollment cache keys |
| `send_result_calculation_mail` | Result calculation request | Email admin with confirmation link |

**Monitor tasks:**
```bash
docker compose exec backend celery -A NexusAPI flower
```

---

## File Storage

All user-uploaded files are stored on Cloudinary:

| Upload Type | Path Pattern |
|-------------|-------------|
| Profile images | `user_images/` |
| Allocation files | `allocations/{id}/uploads/` |
| Assessment files | `allocations/{id}/{assessment_id}/uploads/` |
| Student submissions | `allocations/{id}/{assessment_id}/{enrollment_id}/uploads/` |

Allowed file types: `jpeg`, `png`, `pdf`, `docx`, `pptx`, `xlsx`, `csv`, `zip`
Maximum file size: 50MB

---

## Testing

NexusAPI has a comprehensive test suite with 217+ tests across 5 test files.

### Run all tests:
```bash
docker compose exec backend pytest -v --tb=short
```

### Run with coverage:
```bash
docker compose exec backend pytest \
  --cov=AdminModule \
  --cov=FacultyModule \
  --cov=Models \
  --cov-report=term-missing \
  --cov-config=setup.cfg
```

### Run specific module:
```bash
docker compose exec backend pytest AdminModule/tests/ -v
docker compose exec backend pytest FacultyModule/tests/ -v
```

### Coverage by module:

| Module | Coverage |
|--------|----------|
| `AdminModule/tasks.py` | 94% |
| `Models/models.py` | 93% |
| `AdminModule/serializers.py` | 74% |
| `AdminModule/views.py` | 73% |
| `FacultyModule/views.py` | 74% |
| **Total** | **83%** |

---

## Load Testing

NexusAPI includes three Locust load test scenarios.



### Performance thresholds:

| Metric | Target |
|--------|--------|
| Failure rate | < 5% |
| Average response time | < 1000ms |
| P95 response time | < 3000ms |

---




