from django.urls import path

from . import attachments, auth_views, resource_api, views

urlpatterns = [
    # Authentication / zero-trust
    path("auth/device", auth_views.DeviceStatusView.as_view()),
    path("auth/device/enroll", auth_views.DeviceEnrollView.as_view()),
    path("auth/login", auth_views.LoginView.as_view()),
    path("auth/mfa/setup", auth_views.MfaSetupView.as_view()),
    path("auth/mfa/verify", auth_views.MfaVerifyView.as_view()),
    path("auth/refresh", auth_views.RefreshView.as_view()),
    path("auth/logout", auth_views.LogoutView.as_view()),
    path("auth/me", auth_views.MeView.as_view()),
    path("auth/change-password", auth_views.ChangePasswordView.as_view()),
    path("auth/preferences", auth_views.PreferencesView.as_view()),
    # Metadata-driven resources
    path("meta", resource_api.MetaView.as_view()),
    path("r/<str:key>/meta", resource_api.ResourceMetaView.as_view()),
    path("r/<str:key>/export", resource_api.ResourceExportView.as_view()),
    path("r/<str:key>/actions/<str:action>", resource_api.ResourceActionView.as_view()),
    path("r/<str:key>/<uuid:pk>/actions/<str:action>", resource_api.ResourceActionView.as_view()),
    path("r/<str:key>/<uuid:pk>/history", resource_api.ResourceHistoryView.as_view()),
    path("r/<str:key>/<uuid:pk>/attachments", attachments.AttachmentListView.as_view()),
    path("r/<str:key>/<uuid:pk>", resource_api.ResourceDetailView.as_view()),
    path("r/<str:key>", resource_api.ResourceListView.as_view()),
    path("attachments/<uuid:att_id>/content", attachments.AttachmentContentView.as_view()),
    # Inbox / audit / lookups
    path("notifications", views.NotificationsView.as_view()),
    path("notifications/seen", views.NotificationSeenView.as_view()),
    path("notifications/<uuid:pk>/seen", views.NotificationSeenView.as_view()),
    path("audit", views.AuditListView.as_view()),
    path("audit/verify", views.AuditVerifyView.as_view()),
    path("lookups/<str:source>", views.LookupView.as_view()),
]
