from django.contrib import admin
from .models import NotificationChannel, NotificationReceiver


@admin.register(NotificationChannel)
class NotificationChannelAdmin(admin.ModelAdmin):
    list_display = ('id', 'channel_code', 'channel_name', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('channel_code', 'channel_name')


@admin.register(NotificationReceiver)
class NotificationReceiverAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'channel', 'project', 'is_active', 'created_at')
    list_filter = ('channel', 'is_active')
    search_fields = ('name',)
    raw_id_fields = ('project', 'channel')
