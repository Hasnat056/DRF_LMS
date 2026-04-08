"""
test_reviews.py
----------------
Tests for ReviewListAPIView, ReviewCreateAPIView,
ReviewRetrieveUpdateDestroyAPIView.

URL patterns (note: no app namespace on review-detail, so use raw path):
  /<student_id>/enrollments/reviews/
  /<student_id>/enrollments/<enrollment_id>/reviews/
  /<student_id>/enrollments/<enrollment_id>/reviews/<review_id>/
"""
import pytest
from django.urls import reverse

STUDENT = '/api/student'


def review_list_url(student_id):
    return f'{STUDENT}/{student_id}/enrollments/reviews/'


def review_create_url(student_id, enrollment_id):
    return f'{STUDENT}/{student_id}/enrollments/{enrollment_id}/reviews/'


def review_detail_url(student_id, enrollment_id, review_id):
    return reverse('review-detail', kwargs={
        'student': student_id,
        'enrollment': enrollment_id,
        'review_id': review_id,
    })


@pytest.mark.django_db
class TestReviewList:

    def test_list_200(self, student_client, student_instance):
        sid = student_instance.student_id.person_id
        r = student_client.get(review_list_url(sid))
        assert r.status_code == 200

    def test_list_returns_created_review(
        self, student_client, student_instance, review
    ):
        sid = student_instance.student_id.person_id
        r = student_client.get(review_list_url(sid))
        assert r.status_code == 200
        results = r.data.get('results', r.data)
        ids = [rv['review_id'] for rv in results]
        assert review.review_id in ids

    def test_faculty_can_list_reviews(
        self, faculty_client, student_instance, review
    ):
        sid = student_instance.student_id.person_id
        r = faculty_client.get(review_list_url(sid))
        assert r.status_code == 200

    def test_admin_can_list_reviews(
        self, admin_client, student_instance, review
    ):
        sid = student_instance.student_id.person_id
        r = admin_client.get(review_list_url(sid))
        assert r.status_code == 200


@pytest.mark.django_db
class TestReviewCreate:

    def test_student_can_create_review(
        self, student_client, student_instance, active_enrollment
    ):
        sid = student_instance.student_id.person_id
        url = review_create_url(sid, active_enrollment.enrollment_id)
        r = student_client.post(url, {
            'review_text': 'Excellent!',
            'rating': 5,
        }, format='json')
        assert r.status_code == 201

    def test_review_create_requires_text_and_rating(
        self, student_client, student_instance, active_enrollment
    ):
        sid = student_instance.student_id.person_id
        url = review_create_url(sid, active_enrollment.enrollment_id)
        r = student_client.post(url, {}, format='json')
        assert r.status_code == 400

    def test_faculty_cannot_create_review(
        self, faculty_client, student_instance, active_enrollment
    ):
        sid = student_instance.student_id.person_id
        url = review_create_url(sid, active_enrollment.enrollment_id)
        r = faculty_client.post(url, {
            'review_text': 'Nice.',
            'rating': 4,
        }, format='json')
        assert r.status_code == 403

    def test_review_context_sets_enrollment(
        self, student_client, student_instance, active_enrollment
    ):
        """Review.enrollment is set from URL kwarg, not request body."""
        from Models.models import Reviews
        sid = student_instance.student_id.person_id
        url = review_create_url(sid, active_enrollment.enrollment_id)
        student_client.post(url, {
            'review_text': 'Good one',
            'rating': 4,
        }, format='json')
        assert Reviews.objects.filter(
            enrollment=active_enrollment,
            review_text='Good one',
        ).exists()


@pytest.mark.django_db
class TestReviewRetrieveUpdateDestroy:

    def test_retrieve_review_200(
        self, student_client, student_instance, active_enrollment, review
    ):
        sid = student_instance.student_id.person_id
        url = review_detail_url(sid, active_enrollment.enrollment_id, review.review_id)
        r = student_client.get(url)
        assert r.status_code == 200

    def test_update_review_200(
        self, student_client, student_instance, active_enrollment, review
    ):
        sid = student_instance.student_id.person_id
        url = review_detail_url(sid, active_enrollment.enrollment_id, review.review_id)
        r = student_client.patch(url, {'review_text': 'Updated review', 'rating': 3}, format='json')
        assert r.status_code == 200
        review.refresh_from_db()
        assert review.review_text == 'Updated review'

    def test_student_cannot_delete_review(
        self, student_client, student_instance, active_enrollment, review
    ):
        """ReviewPermission blocks DELETE for all non-superuser."""
        sid = student_instance.student_id.person_id
        url = review_detail_url(sid, active_enrollment.enrollment_id, review.review_id)
        r = student_client.delete(url)
        assert r.status_code == 403

    def test_retrieve_nonexistent_review_404(
        self, student_client, student_instance, active_enrollment
    ):
        sid = student_instance.student_id.person_id
        url = review_detail_url(sid, active_enrollment.enrollment_id, 99999)
        r = student_client.get(url)
        assert r.status_code == 404

    def test_review_fields_present(
        self, student_client, student_instance, active_enrollment, review
    ):
        sid = student_instance.student_id.person_id
        url = review_detail_url(sid, active_enrollment.enrollment_id, review.review_id)
        r = student_client.get(url)
        assert r.status_code == 200
        for field in ('review_id', 'review_text', 'rating', 'timestamp'):
            assert field in r.data
