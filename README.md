# XSM markaziy serveri

Render ushbu papkani repozitoriy ildizi sifatida ishlatadi. Mavjud PostgreSQL manzili va doimiy SECRET_KEY saqlanadi. Yangi qurilmaga techadmin Qurilmalar menyusida bir marta ruxsat beradi.

Windows terminallari chekni o‘z PostgreSQL bazasida saqlaydi. Tasdiqlangan yozuv fon jarayonida HTTPS orqali /api/station/webhook ga boradi. Server hodisani va hisobot yozuvini bir tranzaksiyada saqlaydi. Javob yo‘qolsa, o‘sha hodisani qayta yuborish tushumni takrorlamaydi. KPP va markaziy hisobotlar internetga yuborilgan amallarni ko‘rsatadi.

Server fayllarini yangilash uchun Git for Windows o‘rnating, GitHub hisobingizga kiring. GitHub-yangilash.cmd o‘zgargan fayllarni alfagemini07-del/xsm repozitoriysining main tarmog‘iga XSM Release avtomatik commit muallifi bilan yuboradi va eski server fayllarini olib tashlaydi. Faqat farqlarni ko‘rish uchun python publish_server.py --preview ishlating. Render Auto-Deploy yoqilgan bo‘lsa, keyingi joylashni Render bajaradi.

## Render sozlamalari

Build uchun `pip install -r requirements.txt`, start uchun `gunicorn app:app --workers 1 --threads 4 --timeout 120 --access-logfile -` ishlating. `DATABASE_URL`, `SECRET_KEY` va `COOKIE_SECURE=true` sozlanadi. Yangi bazaga ilk techadmin uchun `INITIAL_TECHADMIN_USERNAME` va `INITIAL_TECHADMIN_PASSWORD` kerak.

/dastur/kpp sahifasi KPP1 va KPP2 manzillari hamda APK yuklash tugmalarini beradi.

Terminal birinchi kirishda online tasdiq oladi. Shundan keyin mahalliy login va to‘lovlar offline ishlaydi. Server bloklash yoki parol o‘zgarishini terminal bilan navbatdagi aloqada yetkazadi. Hisobotlar markazga kelgan yozuvlardan tuziladi. Qurilmalar holati asosiy admin menyusida, 100 soniyalik signal chegarasi bilan ko‘rsatiladi.

Updater majburiy fayllarni ROOT_FILES ro‘yxatidan oladi; yangi ildiz `.py` modullarini avtomatik qo‘shadi. Qo‘shimcha `test_` va `ui_fixtures` modullari joylanmaydi.

GitHub 403 bersa, `GitHub-kirish.cmd` orqali **alfagemini07-del** hisobiga kiring. `GitHub-tekshirish.cmd` yozish huquqini fayllarni yubormasdan tekshiradi. Keyin `GitHub-yangilash.cmd`ni oching. Jurnal: `github-yangilash.log`.

KPP oynalari asl parkovka uslubida: raqam, to‘lov, navbat va chek bitta sahifada. Javobi yo‘qolgan amal qayta yuborilganda aynan oldingi chek qaytariladi. KPP uchun internet kerak; kompyuter terminallari esa ilk tasdiqdan keyin offline ishlaydi.

Sekinlikni kamaytirish: foydalanuvchi/qurilma tekshiruvi birlashtirilgan, KPP hisobi bitta so‘rovda olinadi, hisobot so‘rovlari guruhlangan, health natijasi 20 soniya saqlanadi. Admin so‘rovlari faqat kerakli menyuda bajariladi. Render va baza hududlarini tekshiring. Free tarifdagi uyqu cheklovi: https://render.com/docs/free .
