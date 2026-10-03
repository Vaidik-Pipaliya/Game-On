from django.contrib import admin

from .models import MenuItem, Shift, Tab, TabLine, Table

admin.site.register([MenuItem, Table, Tab, TabLine, Shift])
