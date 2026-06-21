from .models import ChatMessage, Notification, UserProfile


def unread_messages(request):

    user_id = request.session.get('user_id')

    unread_count = 0
    unread_notification_count = 0
    recent_notifications = []

    if user_id:

        try:

            user = UserProfile.objects.get(
                id=user_id
            )

            unread_count = ChatMessage.objects.exclude(
                hidden_for=user
            ).filter(
                receiver=user,
                is_read=False
            ).count()

            unread_notification_count = Notification.objects.filter(
                user=user,
                is_read=False
            ).count()

            recent_notifications = Notification.objects.filter(
                user=user
            ).order_by(
                '-created_at'
            )[:5]

        except UserProfile.DoesNotExist:

            unread_count = 0

    return {

        'unread_count': unread_count,
        'unread_message_count': unread_count,
        'unread_notification_count': unread_notification_count,
        'recent_notifications': recent_notifications

    }
