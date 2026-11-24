# معماری

```
Browser (React/TS)  ──HTTP /api/v1──►  FastAPI (localhost)  ──►  Services ──► Engine ──► VendorAdapter ──► Transport(SSH/Netmiko | Simulator)
                                           │                                         
                                           └─ SQLAlchemy/SQLite (Alembic migrations), SecretBox (DPAPI/Fernet)
```

- **فرانت‌اند** هیچ منطق فروشنده‌ای ندارد. فرم‌ها از `GET /api/v1/modules/schema` (JSON Schema خودِ مدل‌های Pydantic) ساخته می‌شوند و قابلیت‌ها از `GET /devices/{id}/modules` می‌آید؛ ماژول ناپشتیبان با دلیل نشان داده می‌شود.
- **مدل نرمال** (`nc/engine/specs.py`): درخواست‌ها با Pydantic اعتبارسنجی می‌شوند (محدودهٔ عدد، CIDR، وابستگی فیلدها، حروف مجاز تا هیچ مقداری نتواند خط CLI اضافه کند).
- **آداپتور فروشنده** (`nc/vendors/*`): `identify_commands/parse_identity`، `probes`، `translate(spec, ctx) -> Plan`، `backup_commands`، `parse_metrics/neighbors`، `diag_command`. هر `Plan` شامل عملیات، `checks` (راستی‌آزمایی regex روی خروجی خواندنی)، `existence` (آیا شیء قبلاً بود؟)، `rollback`.
- **موتور** (`nc/engine/executor.py`): پشتیبانی ماژول ← ترجمه ← ریسک ← خواندن وضعیت فعلی ← (compliant؟) ← قفل دستگاه ← پشتیبان ← اعمال ← تشخیص خطای دستگاه ← verify ← rollback/گزارش. قطع اتصال = وضعیت `unknown_state` (ادعای رول‌بک نمی‌شود).
- **Transport**: SSH با Netmiko؛ شبیه‌ساز (`nc/simulator.py`) فقط با `NC_ENABLE_SIMULATOR=1` و در تست‌ها، و در UI با برچسب SIMULATED.
- **پایگاه داده**: جدول‌ها در `nc/models.py`؛ مهاجرت‌ها `backend/alembic/versions` (هنگام شروع خودکار `upgrade head`).
- **امنیت**: `nc/security/` (توکن دسترسی محلی، کوکی نشست HttpOnly، CSRF دوبل‌سابمیت، بررسی Host/Origin، SecretBox).
- **کارها**: `nc/services/jobs.py` با ThreadPool؛ قفل per-device مانع دو تغییر همزمان روی یک دستگاه می‌شود.

## قابلیت (Capability) به‌جای فرض
هر دستگاه پس از «Identify» ماتریس `capabilities` دارد: `supported / partial / unsupported / not_detected / requires_privilege` به‌همراه دلیل و دستور probe. هر ماژول پیش از برنامه‌ریزی همین ماتریس را بررسی می‌کند.

## نگاشت مفاهیم متفاوت (نه یکسان‌سازی ساختگی)
| مفهوم | Cisco | MikroTik | FortiGate |
|---|---|---|---|
| VLAN | پایگاه VLAN (`vlan N`) + SVI | `/interface vlan` | `config system interface` (زیرواسط VLAN) |
| NTP | `ntp server` | v7: `servers add`، v6: primary/secondary | `config system ntp` |
| SNMP community | `snmp-server community` | `/snmp community` | نیازمند host مجاز |
