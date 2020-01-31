""" Admin configuration for core models. """

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import ugettext_lazy as _
from guardian.admin import GuardedModelAdmin

from .models import (
    Organization,
    OrganizationGroup,
    PendingUserGroup,
    Program,
    ProgramOrganizationGroup,
    User,
)


class CustomUserAdmin(UserAdmin):
    """ Admin configuration for the custom User model. """
    list_display = ('id', 'username', 'email', 'full_name', 'first_name', 'last_name', 'is_staff')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        (_('Personal info'), {'fields': ('full_name', 'first_name', 'last_name', 'email')}),
        (_('Permissions'), {'fields': ('is_active', 'is_staff', 'is_superuser',
                                       'groups', 'user_permissions')}),
        (_('Important dates'), {'fields': ('last_login', 'date_joined')}),
    )


class OrganizationAdmin(GuardedModelAdmin):
    list_display = ('key', 'name', 'discovery_uuid')
    search_fields = ('key', 'name')
    ordering = ('key',)
    date_hierarchy = 'modified'


class OrganizationGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', 'role')
    exclude = ('permissions',)


class PendingUserGroupAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'group')
    search_fields = ('user_email', )


class ProgramAdmin(admin.ModelAdmin):
    """
    Admin tool for the ProgramEnrollment model
    """
    list_display = ("key", "discovery_uuid", "managing_organization")


class ProgramGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'program', 'granting_organization', 'role')
    exclude = ('permissions', )


admin.site.register(User, CustomUserAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(OrganizationGroup, OrganizationGroupAdmin)
admin.site.register(PendingUserGroup, PendingUserGroupAdmin)
admin.site.register(Program, ProgramAdmin)
admin.site.register(ProgramOrganizationGroup, ProgramGroupAdmin)
