"""Terminal strings (English / Persian). Persian is reshaped + bidi'd so it shows correctly in terminals."""
import config

FA = {
    "subtitle": "دستیار شخصی هوشمند  •  پنل مدیریت",
    "status": "وضعیت", "menu": "منو",
    "ai": "هوش مصنوعی", "bot": "ربات تلگرام", "owner": "مالک", "account": "اکانت تلگرام",
    "tz": "منطقه زمانی", "service": "سرویس", "web": "پنل وب", "approval": "حالت تایید",
    "set": "تنظیم شده", "unset": "تنظیم نشده", "linked": "متصل", "unlinked": "متصل نیست",
    "running": "در حال اجرا", "stopped": "متوقف", "loggedin": "وارد شده", "notin": "وارد نشده",
    "m_quick": "نصب سریع (مرحله به مرحله)", "m_ai": "هوش مصنوعی و کلید API",
    "m_bot": "توکن ربات تلگرام", "m_pair": "اتصال مالک (اکانت شما)",
    "m_login": "ورود اکانت تلگرام (اختیاری)", "m_web": "پنل وب (رمز و دسترسی)",
    "m_settings": "تنظیمات (زبان، زمان، تایید)", "m_start": "شروع / ری‌استارت",
    "m_stop": "توقف", "m_logs": "لاگ زنده", "m_doctor": "عیب‌یابی خودکار (Doctor)", "m_update": "آپدیت از گیت‌هاب",
    "m_uninstall": "حذف کامل", "m_exit": "خروج", "choose": "انتخاب کنید", "enter": "برای برگشت Enter بزنید",
}
EN = {
    "subtitle": "Personal AI Butler  •  Control Panel",
    "status": "Status", "menu": "Menu",
    "ai": "AI model", "bot": "Telegram bot", "owner": "Owner", "account": "Telegram account",
    "tz": "Timezone", "service": "Service", "web": "Web panel", "approval": "Approval",
    "set": "Configured", "unset": "Not set", "linked": "Linked", "unlinked": "Not linked",
    "running": "Running", "stopped": "Stopped", "loggedin": "Logged in", "notin": "Not logged in",
    "m_quick": "Quick Setup (step by step)", "m_ai": "AI provider & API key",
    "m_bot": "Telegram bot token", "m_pair": "Link owner (your Telegram)",
    "m_login": "Login Telegram account (optional)", "m_web": "Web panel (password & access)",
    "m_settings": "Settings (language, time, approval)", "m_start": "Start / Restart",
    "m_stop": "Stop", "m_logs": "Live logs", "m_doctor": "Doctor (find & fix problems)", "m_update": "Update from GitHub",
    "m_uninstall": "Uninstall", "m_exit": "Exit", "choose": "Choose", "enter": "Press Enter to go back",
}


def lang() -> str:
    return config.load().get("lang", "fa")


def shape(s: str) -> str:
    if lang() != "fa":
        return s
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(s))
    except Exception:
        return s


def t(key: str) -> str:
    d = FA if lang() == "fa" else EN
    return shape(d.get(key, EN.get(key, key)))
