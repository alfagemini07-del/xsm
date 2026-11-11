# XSM markaziy serveri

## Hudud nazorati va katta ekran

Ochiq E-navbat tablosi: `https://xsm.onrender.com/tablo`. Login talab qilinmaydi. Unda tashqi parkovkada hali chiqishi qayd etilmagan Ayritom va Free Zone yo‘nalishlaridagi dastlabki 10 ta avtomobil ko‘rsatiladi; birinchi 5 tasi katta urg‘u bilan ajratiladi. Parkovka operatori chiqishni tasdiqlaganda avtomobil tablodan tushadi va qolgan navbatlar avtomatik yuqoriga siljiydi. Ochiq API faqat davlat raqami, navbat raqami, yo‘nalish va tartibni beradi; to‘lov hamda xodim ma’lumotlarini bermaydi.

Admin menyusida **Hudud nazorati**ni oching. Manzili `/admin/control-room`. **To‘liq ekran** tugmasi xarita va bugungi tushumni katta monitorda ko‘rsatadi. Faqat kuzatish uchun techadmin foydalanuvchiga **3D kuzatuvchi** rolini beradi. Bu login bilan kirilganda to‘g‘ridan-to‘g‘ri xarita ochiladi. Navbat chaqirish, joy almashtirish va sozlamalarni tahrirlash vakolati adminlarda bo‘ladi. **Navbat tablo ekrani** roli esa haydovchilarga mo‘ljallangan `/admin/queue-display` sahifasini ochadi.

| Xarita ko‘rsatkichi | Raqam nimani bildiradi |
|---|---|
| E-navbat parkovka | Kirishi qayd etilgan, parkovkadan chiqish qaydi hali kelmagan avtomobillar |
| Free Zone | Darvozadan kirgan va hududdan chiqishi qayd etilmagan avtomobillar |
| Tarozi | Bugun to‘lovi qayd etilgan turli davlat raqamlari. Qayta tortilgan mashina bir marta sanaladi |
| Chegara yo‘nalishi | Parkovkada chegara yo‘nalishida kutayotganlar. Bu son parkovka jami ichiga kiradi |
| O‘zbekiston darvozasi va Afg‘oniston darvozasi | Toshkent vaqti bilan bugun darvozadan kirish va chiqish qaydlari. Bir avtomobil ikki marta o‘tsa, ikkala o‘tish ham hisoblanadi |

Hisoblagichni bosib o‘sha joyni yaqindan ko‘ring. To‘q ko‘k xaritada parkovka moviy, Free Zone binafsha, chegara shaftoli rangida ajratilgan. Son o‘zgarganda qisqa animatsiya ko‘rinadi. Brauzerda harakatni kamaytirish sozlangan bo‘lsa, yangi raqam darhol chiqadi.

**Hudud bandligi** parkovkadan chiqqan, ammo O‘zbekiston darvozasida kirishi hali qayd etilmagan mashinalarni **yo‘lda** ko‘rsatadi. **Hudud sozlamalari**da techadmin bandlik ogohlantirishlarini belgilaydi. Dastlabki chegaralar 80% va 90%. Barcha joylar band yoki rezerv bo‘lsa, bo‘sh joy qolmagani ko‘rinadi.

**Demo** orqali FZ navbatini chaqiring, tanlangan mashinani kiriting va keyin chegaraga chiqaring. Parkovka soni kamayishi, Free Zone soni oshishi va darvoza hisoblagichi o‘zgarishini kuzating. Demo tushumlari faqat shu brauzerda hisoblanadi. **Jonli rejim** tugmasi ishchi qaydlarga qaytaradi.

Xaritani sichqoncha yoki barmoq bilan kerakli tomonga burang va **Ko‘rinishni saqlash**ni bosing. Shu brauzer keyingi ochilishda tanlangan kamera holatini tiklaydi. **Tejamkor** sifati kichik grafik protsessorli kompyuterlarga mo‘ljallangan. Sifat tanlovi ham shu brauzerda saqlanadi.

Ekran ochiq turganda yangilanish oralig‘i taxminan 6,5 soniya. Ma’lumot vaqti sarlavhada ko‘rinadi. O‘zgarmagan ma’lumot uchun server bo‘sh javob qaytaradi. Aloqa uzilsa, oxirgi o‘qilgan holat ekranda qoladi. Avtomobil yo‘li postda qayd etilgan harakatning tasviridir. Mahalliy terminal offline bo‘lsa, uning yangi cheki sinxronlashdan keyin xaritada va hisobotda ko‘rinadi.

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

Katta tarix uchun **Siqilgan zaxira (.zip)** tugmasini ishlating. Server jadvallarni ketma-ket o‘qib, bitta vaqt holatidagi arxivni tayyorlaydi. Importda ZIP faylning o‘zini tanlang, **Faylni tekshirish**ni bosing va natijadagi yangi yozuvlar sonini ko‘ring. Keyin **Tekshirilgan faylni import qilish**ni bosing. ZIP chegarasi siqilganda 250 MB, ochilganda 512 MB. Yozuvlar tekshiruvdan o‘tmasa, import tranzaksiyasi bekor qilinadi.
