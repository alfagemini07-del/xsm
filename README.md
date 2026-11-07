# XSM markaziy serveri

Render ushbu papkani repozitoriy ildizi sifatida ishlatadi. Mavjud PostgreSQL manzili va doimiy SECRET_KEY saqlanadi. Yangi qurilmaga techadmin Qurilmalar menyusida bir marta ruxsat beradi.

Windows terminallari chekni o‘z PostgreSQL bazasida saqlaydi. Tasdiqlangan yozuv fon jarayonida HTTPS orqali /api/station/webhook ga boradi. Server hodisani va hisobot yozuvini bir tranzaksiyada saqlaydi. Javob yo‘qolsa, o‘sha hodisani qayta yuborish tushumni takrorlamaydi. KPP va markaziy hisobotlar internetga yuborilgan amallarni ko‘rsatadi.

Server fayllarini yangilash uchun Git for Windows o‘rnating, GitHub hisobingizga kiring. GitHub-yangilash.cmd o‘zgargan fayllarni alfagemini07-del/xsm repozitoriysining main tarmog‘iga XSM Release avtomatik commit muallifi bilan yuboradi va eski server fayllarini olib tashlaydi. Faqat farqlarni ko‘rish uchun python publish_server.py --preview ishlating. Render Auto-Deploy yoqilgan bo‘lsa, keyingi joylashni Render bajaradi.

## Render sozlamalari

Build uchun `pip install -r requirements.txt`, start uchun `gunicorn app:app --workers 1 --threads 4 --timeout 120 --access-logfile -` ishlating. `DATABASE_URL`, `SECRET_KEY` va `COOKIE_SECURE=true` sozlanadi. Yangi bazaga ilk techadmin uchun `INITIAL_TECHADMIN_USERNAME` va `INITIAL_TECHADMIN_PASSWORD` kerak.

/dastur/kpp sahifasi Afg‘oniston darvozasi (KPP1) va O‘zbekiston darvozasi (KPP2) manzillari hamda APK yuklash tugmalarini beradi. Foydalanuvchi loginlari va ayritom/kpp2 ichki rollari saqlangan.

Terminal birinchi kirishda online tasdiq oladi. Shundan keyin mahalliy login va to‘lovlar offline ishlaydi. Server bloklash yoki parol o‘zgarishini terminal bilan navbatdagi aloqada yetkazadi. Hisobotlar markazga kelgan yozuvlardan tuziladi. Qurilmalar holati asosiy admin menyusida, 100 soniyalik signal chegarasi bilan ko‘rsatiladi.

Updater majburiy fayllarni ROOT_FILES ro‘yxatidan oladi; yangi ildiz `.py` modullarini avtomatik qo‘shadi. Qo‘shimcha `test_` va `ui_fixtures` modullari joylanmaydi.

GitHub 403 bersa, `GitHub-kirish.cmd` orqali **alfagemini07-del** hisobiga kiring. `GitHub-tekshirish.cmd` yozish huquqini fayllarni yubormasdan tekshiradi. Keyin `GitHub-yangilash.cmd`ni oching. Jurnal: `github-yangilash.log`.

KPP oynalari asl parkovka uslubida: raqam, to‘lov, navbat va chek bitta sahifada. Javobi yo‘qolgan amal qayta yuborilganda aynan oldingi chek qaytariladi. KPP uchun internet kerak; kompyuter terminallari esa ilk tasdiqdan keyin offline ishlaydi.

Sekinlikni kamaytirish: foydalanuvchi/qurilma tekshiruvi birlashtirilgan, KPP hisobi bitta so‘rovda olinadi, hisobot so‘rovlari guruhlangan, health natijasi 20 soniya saqlanadi. Admin so‘rovlari faqat kerakli menyuda bajariladi. Render va baza hududlarini tekshiring. Free tarifdagi uyqu cheklovi: https://render.com/docs/free .

## KPP va hisobotlar

`static/apk/kpp1.apk` — **Afg‘oniston darvozasi**, `static/apk/kpp2.apk` — **O‘zbekiston darvozasi**. Android launcherida ham shu nomlar ko‘rinadi. SUNMI V2s Smartda tik holatda ochiladi va ichki printerdan foydalanadi. KPP cheki 58 mm; E-navbat parkovka va tarozi Windows cheklari 80 mm. Chop natijasi hamda uzilgan aloqadagi qayta yuborishlar jurnalga yoziladi. KPP kirish-chiqishida server aloqasi zarur.

Qidiruv va hisobotlar barcha to‘rtta nuqtani qamrab oladi. Excel amal va markazga kelish vaqtlarini, xodimni, to‘lov sababi va xizmatlar summalarini ko‘rsatadi. Hisobot serverdagi oxirgi ma’lumotga asoslanadi. Aloqasi yo‘q terminalning navbatidagi yozuvlar kelgach qo‘shiladi.

Import va backup menyusida umumiy JSON eksport, faylni tekshirish va techadmin importi mavjud. Mavjud yozuv ustiga yozilmaydi. Maxfiy kalit va parollar ko‘chirilmaydi. Eski tarozi SQLite fayli alohida import qilinadi. Qurilmalar menyusida ulanish tarixi sahifalarga bo‘lingan. Tasdiqlangan Windows terminali o‘sha Windows foydalanuvchisi bilan keyingi ishga tushirishda avtomatik kiradi.
