from django.contrib import admin
from .models import NotificationChannel, NotificationReceiver


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel_code', 'channel_name', 'is_active', 'created_at')
    readonly_fields = ('channel_code', 'channel_name', 'description', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(channel_code='email')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NotificationReceiver)
class NotificationReceiverAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'channel', 'project', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(channel__channel_code='email')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
