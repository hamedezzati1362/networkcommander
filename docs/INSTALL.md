# نصب

## کاربر نهایی (ویندوز ۱۰/۱۱) — **هنوز آزموده‌نشده**
نصب‌کنندهٔ `NetworkCommander-Setup-<نسخه>.exe` از GitHub Actions (workflow «Network Commander»، artifact `NetworkCommander-Setup`) ساخته می‌شود. تا اولین اجرای موفق آن workflow و آزمون روی ویندوز واقعی، آن را «تأییدشده» ندانید.

1. نصب‌کننده را اجرا کنید (نیاز به ادمین ندارد؛ در پروفایل کاربر نصب می‌شود). Python/Node لازم نیست؛ همه‌چیز بسته‌بندی شده.
2. گزینه‌ها: میان‌بر دسکتاپ، «اجرای خودکار هنگام ورود به ویندوز».
3. «Network Commander» از منوی Start مرورگر را روی `http://127.0.0.1:8765` باز می‌کند (لینک یک‌بارمصرف نشست). «Stop Network Commander» سرویس را متوقف می‌کند.
4. داده‌ها: `%LOCALAPPDATA%\NetworkCommander` (دیتابیس، لاگ‌ها در `logs\`). پایگاه داده خودکار ساخته و مهاجرت می‌شود.

**به‌روزرسانی:** نصب‌کنندهٔ جدید را روی قبلی اجرا کنید؛ داده‌ها دست‌نخورده می‌مانند و مهاجرت‌ها هنگام شروع اجرا می‌شوند. **حذف:** از Apps & features؛ داده‌ها حذف نمی‌شوند (برای حذف کامل پوشهٔ داده را پاک کنید).

## انتشار ریلیز (برای مالک پروژه)
با پوش تگ `v*` (مثلاً `v0.1.0`) workflow «Network Commander» تست می‌کند، نصب‌کننده را در ویندوز می‌سازد، smoke test می‌زند و آن را همراه فایل `.sha256` به‌صورت **pre-release** در GitHub Releases منتشر می‌کند. تا زمانی که Actions اجرا نشود (و این مسیر آزموده نشود) هیچ ریلیزی منتشر نشده است. پیش از اعتماد، sha256 را با فایل پیوست مقایسه کنید.

## توسعه (لینوکس/ویندوز)
```bash
cd backend && pip install -r requirements-dev.txt && python -m pytest -q
cd ../frontend && npm ci && npm run build
cd ../backend && NC_FRONTEND_DIR=../frontend/dist python -m nc.main
```
متغیرها: `NC_DATA_DIR`, `NC_PORT`, `NC_HOST` (پیش‌فرض 127.0.0.1)، `NC_ENABLE_SIMULATOR=1`, `NC_FRONTEND_DIR`.

## ساخت دستی روی ویندوز
`powershell -ExecutionPolicy Bypass -File installer\build-windows.ps1` (Python 3.12، Node 22، و برای نصب‌کننده Inno Setup).
