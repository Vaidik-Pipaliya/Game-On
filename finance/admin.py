from django.contrib import admin

from .models import Invoice, Ledger, Payment

admin.site.register([Payment, Ledger, Invoice])
