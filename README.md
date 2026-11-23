# BIOTA Sales Dashboard

Dashboard penjualan dari file ekspor pesanan Shopee, TikTok Shop dan Tokopedia. Upload file CSV atau Excel, lalu dashboard menampilkan:

- Net revenue, jumlah pesanan, unit terjual, dan pesanan batal/retur
- Net revenue per hari (skala Rupiah, garis rata-rata, hari tertinggi ditandai, angka muncul saat kursor diarahkan) dan per channel
- 10 produk teratas (unit terjual + net revenue), plus semua produk
- **Setiap tabel punya filter**: pencarian, rentang tanggal sendiri, channel, kategori produk (Kamera CCTV, Perlengkapan hewan, Kartu memori, Lampu; dibaca dari awalan SKU), status pesanan, dan halaman per 10
- **Klik untuk detail, sampai ke pesanannya**: setiap angka, produk, channel, hari dan temuan bisa diklik. Rinciannya bisa diklik lagi sampai ke daftar pesanan di baliknya (mis. Smart Pet Feeder → 4 Oktober → 2 pesanan), lengkap dengan pencarian dan halaman
- Filter tanggal (tombol cepat 1-15 dan 16-31, atau pilih tanggal sendiri)
- **Temuan yang perlu ditindaklanjuti**: masalah di balik angka total, lengkap dengan saran
- **Upload mingguan digabung dengan data lama**: pesanan baru ditambahkan, status yang berubah diperbarui (mis. "Dikirim" minggu lalu, "Selesai" sekarang), dan baris yang sudah ada tidak dihitung dua kali
- Kata sandi tim, supaya data penjualan tidak terbuka untuk siapa pun yang punya link

Susunannya mengikuti dashboard Seller Centre yang dipakai tim setiap hari, supaya langsung terbaca, juga di HP.

## Cara mencoba

- **Link live:** https://biota-sales-dashboard.vercel.app &middot; kata sandi tim: **0987**
- **Video penjelasan (2,5 menit):** _(link)_
- Upload `biota_orders_oct2026.csv`, lalu coba filter tanggal 1-15 Oktober, klik angka atau produk mana pun sampai ke daftar pesanannya, dan upload file yang sama sekali lagi: tidak ada yang dihitung dua kali.

## Inti vs tambahan

Brief meminta dibuat sesederhana mungkin, jadi bagian inti sengaja dipisahkan dari tambahan.

**Inti (yang diminta brief)** ada di satu file, `biota/analysis.py`: membaca CSV, merapikan dan menggabungkan tiga channel, menghitung net revenue per channel dan per produk, 10 produk teratas, dan filter tanggal. Angka akhirnya dikunci di test.

**Tambahan**, masing-masing dengan alasan bisnis:

| Tambahan | Alasan |
|---|---|
| Upload mingguan digabung (`biota/storage.py`) | Tim menarik data rutin; tanpa ini, setiap upload menimpa data lama atau menghitung dua kali |
| Pertanyaan saat format berubah + saran AI (`biota/ai.py`) | Ekspor marketplace sering berubah; lebih aman bertanya daripada menebak diam-diam |
| Temuan yang perlu ditindaklanjuti | Total saja tidak menunjukkan masalah: Rp61 juta batal/retur, 38 pesanan tersangkut, peluang bundling |
| Klik sampai ke daftar pesanan | Setiap angka bisa dibuktikan, bukan hanya dipercaya |
| Kata sandi tim | Data penjualan perusahaan tidak boleh terbuka untuk siapa pun yang punya link |

## Cara menjalankan

Butuh Python 3.10 atau lebih baru.

```bash
pip install -r requirements.txt
uvicorn api.index:app --reload
```

Buka **http://localhost:8000**, lalu upload file. Data tersimpan di `storage/biota.db`, jadi setelah halaman dimuat ulang dashboard langsung tampil, dan upload berikutnya digabung ke data yang sudah ada.

Pengaturan (opsional): salin `.env.example` menjadi `.env` lalu isi, atau pakai environment variable. File `.env` tidak ikut ke repository.

| Variabel | Fungsi |
|---|---|
| `APP_PASSWORD` | Kata sandi tim. Jika diisi, dashboard meminta kata sandi sebelum data tampil. **Wajib diisi untuk link live.** |
| `DB_PATH` | Lokasi file database SQLite untuk di laptop (default `storage/biota.db`) |
| `DATABASE_URL` | Alamat Postgres (mis. Supabase). Jika diisi, data disimpan di sana, bukan di SQLite. Dipakai untuk link live |
| `GEMINI_API_KEY` | Mengaktifkan saran AI untuk kolom/label yang tidak dikenal (tier gratis cukup) |

Link live berjalan di **Vercel** (halaman + API Python sebagai serverless function). Serverless tidak punya disk permanen, jadi di link live data yang diupload hanya tersimpan sementara (cukup untuk mencoba: upload CSV, lihat hasilnya). Untuk pemakaian sungguhan, isi `DATABASE_URL` dengan Postgres (mis. Supabase); kodenya sudah mendukung dan tidak perlu diubah.

Test:

```bash
pip install pytest httpx
python -m pytest                       # semua test, tanpa memanggil AI sungguhan
RUN_AI_TESTS=1 python -m pytest -k live   # ikut menguji Gemini sungguhan (butuh GEMINI_API_KEY)
python -m tests.dummy_data             # menulis 2 file ekspor dummy ke tests/dummy_out/ untuk dicoba manual
```

Yang diuji:

- Setiap aturan pembersihan dan angka akhir Oktober.
- **Data dummy mingguan baru** (`tests/dummy_data.py`): dua ekspor November yang sengaja berantakan (nama kolom berbeda, file Excel, channel dan status baru, tanggal rusak, produk baru tanpa SKU, data tumpang tindih, status yang berubah, baris ganda). Jawaban benarnya dihitung langsung dari data asal pembuatnya, bukan memakai kode aplikasi, lalu dibandingkan dengan hasil aplikasi. Diuji dengan beberapa data acak.
- **AI tanpa internet**: yang dikirim ke AI hanya nama kolom dan label (tidak ada no. pesanan, harga, kota), saran AI tidak pernah dipakai tanpa konfirmasi, AI yang sibuk dicoba ulang, dan batas kuota dijelaskan di layar.
- **AI sungguhan** (opsional): nama kolom Indonesia/Inggris, variasi channel (Lazada dan Blibli harus ditolak), dan status yang menjebak ("Belum Bayar" tidak boleh dianggap selesai).

Data pesanan sengaja **tidak** ikut di repository karena rahasia. Untuk menjalankan test angka akhir, taruh file di `data/biota_orders_oct2026.csv` (tanpa file itu, test tersebut dilewati).

## Angka akhir (Oktober 2026)

| | Net revenue |
|---|---:|
| **Total** | **Rp298.937.000** |
| Shopee | Rp180.315.000 |
| TikTok Shop | Rp70.461.000 |
| Tokopedia | Rp48.161.000 |
| **1-15 Oktober** | **Rp146.226.000** |

Dari 366 pesanan, 473 unit terjual. 74 pesanan (Rp61.161.000) batal atau retur dan tidak dihitung.

Angka ini dihitung dengan pandas, lalu dicocokkan dengan hitungan kedua yang ditulis terpisah dengan cara berbeda. Hasilnya sama, dan angkanya dikunci di test (`tests/test_analysis.py`).

## Temuan dan saran

Menjumlahkan net revenue hanya menjawab "berapa". Data ini juga menunjukkan beberapa masalah yang kemungkinan terus berulang. Semua temuan dihitung ulang di dashboard setiap kali filter tanggal diubah.

1. **17% nilai pesanan hilang karena batal dan retur (Rp61,2 juta).** Tingkat batal hampir sama di ketiga channel (sekitar 11% pesanan), jadi penyebabnya kemungkinan ada di internal (stok, waktu proses, COD), bukan di salah satu marketplace. Retur paling banyak di Shopee (7% pesanan, channel lain sekitar 3%). Produk dengan porsi batal/retur tertinggi: Lumi Hexagon (33%), Indoor PTZ (23%), Baby Camera (20%).
   *Saran:* minta ekspor yang menyertakan alasan pembatalan, dan cek stok serta waktu proses untuk tiga produk itu.
2. **38 pesanan senilai Rp29,7 juta masih berstatus "dikirim" lebih dari 7 hari**, sebagian sejak 1 Oktober. Pesanan ini sudah masuk net revenue tapi belum pasti diterima. Kemungkinannya, paket tersangkut atau file ekspor diambil sebelum status diperbarui.
   *Saran:* ekspor ulang beberapa hari setelah tutup bulan, dan cek resi yang lama.
3. **Hanya 7 dari 218 pesanan kamera yang ikut membeli microSD (3%)**, padahal CCTV butuh kartu memori untuk merekam.
   *Saran:* coba paket bundling kamera + microSD.
4. **Hari terlaris adalah Minggu 4 Oktober (Rp21,9 juta, 2,5× hari biasa), dan TikTok Shop paling besar hari itu.** Tanggal 10.10 justru di bawah rata-rata.
   *Saran:* cari tahu apa yang terjadi tanggal 4 (live, promo, iklan) supaya bisa diulang.
5. **Masalah proses data:** 22 baris tercatat dua kali (kemungkinan dari menggabung file secara manual), dan 26 listing di ketiga toko belum diisi SKU penjual. Dashboard bisa menanganinya, tapi lebih baik diperbaiki dari sumbernya. Daftar listing tanpa SKU ada di dashboard.

## Keputusan dan asumsi tentang data

1. **Nama channel ditulis berbeda-beda** (10 cara, mis. `shopee`, `Tokped`, `Tiktok`), disatukan menjadi 3 channel. Awalan nomor pesanan (SHP, TTS, TKP) cocok dengan channel di semua baris, jadi penggabungan ini aman.
2. **Tiga format tanggal.** Shopee `2026-10-21 08:24`, TikTok Shop `14/10/2026 23:26:00`, Tokopedia `Oct 04, 2026`. TikTok dibaca **tanggal dulu, baru bulan** (02/10 = 2 Oktober). Data membuktikannya: ada tanggal seperti 14/10 dan 31/10.
3. **Lima format harga** (`Rp1.099.000`, `Rp 1.099.000`, `1,099,000`, `IDR 349,000`, `1099000`). Rupiah di sini tidak punya sen, jadi titik dan koma adalah pemisah ribuan. Untuk file Excel, angka seperti `1099000.0` dibaca sebagai 1.099.000, bukan 10.990.000.
4. **12 label status, 4 kelompok.**
   - Dihitung: selesai (`Selesai`, `Completed`, `Pesanan Selesai`) dan dalam pengiriman (`Dikirim`, `Sedang Dikirim`, `In transit`).
   - Tidak dihitung: batal (`Batal`, `Cancelled`, `Dibatalkan`) dan retur (`Pengembalian Dana`, `Refunded`, `Dikomplain - Retur`).
   - Asumsi: `Dikomplain - Retur` dianggap retur (hanya 2 baris). Kalau komplain masih bisa ditolak, perlu dikonfirmasi ke tim.
5. **22 baris duplikat** (nomor pesanan, SKU, jumlah, total, status dan tanggal sama persis) dihitung sekali. Perbandingan dilakukan setelah data dirapikan, supaya `Shopee` vs `SHOPEE` tidak menyembunyikan duplikat. Asumsi: satu pesanan tidak pernah berisi dua baris identik yang memang sah.
6. **76 baris tanpa SKU** dilengkapi lewat nama produk yang sama persis di baris lain yang punya SKU.
7. **Satu produk punya tiga nama listing** (satu per channel), jadi produk digabung berdasarkan SKU.
8. **MicroSD V-GeN** tercatat dengan SKU internal BIOTA (`BIO-SD-V128`), jadi tetap dihitung.
9. **Net revenue memakai `total_price`.** `qty × unit_price` selalu sama dengan `total_price`. Biaya admin marketplace, voucher dan ongkir tidak ada di file, jadi uang yang benar-benar masuk ke rekening akan lebih kecil. Untuk angka itu perlu laporan penghasilan/settlement dari tiap marketplace.
10. **Filter tanggal memakai tanggal pesanan dibuat**, kedua ujungnya ikut dihitung, tanpa konversi zona waktu (diasumsikan WIB).
11. **Baris yang tidak bisa dibaca tidak pernah diam-diam dihitung.** Baris seperti itu ditolak dan dicantumkan nomor barisnya di catatan data.

## Upload mingguan

Tim menarik data tiap minggu, jadi setiap upload digabung ke data yang sudah tersimpan:

- Setiap baris pesanan dikenali dari **ID baris marketplace** kalau ekspornya punya kolom itu (mis. "Order Item ID"), atau dari **nomor pesanan + SKU + urutan** (baris pertama, kedua... dari SKU itu di pesanan tersebut).
- Baris yang baru pertama kali muncul ditambahkan.
- Baris yang sudah ada tapi berubah (status, tanggal, **jumlah atau harga**) diperbarui. Ekspor terbaru selalu menang: pesanan yang minggu lalu "Dikirim" dan sekarang "Selesai" (atau ternyata diretur) ikut terkoreksi, dan pembatalan sebagian (qty 2 jadi 1) tidak meninggalkan baris lama.
- Baris yang sama persis dilewati, jadi upload ulang atau ekspor yang tumpang tindih tidak menggandakan angka.
- **Pesanan lama yang tidak ada lagi di file terbaru** (di rentang tanggal dan channel yang sama) ditampilkan setelah upload. Pesanan itu **tidak dihapus otomatis**, karena ekspor yang difilter (mis. hanya status "Selesai") akan menghapus data yang benar. Pengguna memilih "Biarkan" atau "Hapus" (dengan konfirmasi), dan penghapusan tercatat di riwayat.
- **Dua baris identik dalam satu pesanan**: tanpa kolom ID baris, tidak bisa dibedakan dari ekspor ganda, jadi dihitung sekali dan nomor barisnya ditampilkan di catatan untuk dicek. Dengan kolom ID baris, hanya baris dengan ID yang sama yang dianggap ganda.
- Jawaban untuk kolom/channel/status yang tidak dikenal disimpan, jadi tidak ditanyakan lagi minggu berikutnya.
- SKU kosong bisa dilengkapi dari nama produk yang pernah muncul di upload sebelumnya.
- Setiap upload tercatat di "Riwayat upload" (berapa baris baru, diperbarui, dan sama).

## Kalau format file berubah, dan peran AI

Ekspor marketplace sering berubah: nama kolom diganti, ada status baru, atau tim mengirim file Excel. Cara aplikasi ini menanganinya:

1. **Aturan dulu.** Nama kolom umum dikenali otomatis (mis. `No. Pesanan`, `Order ID`, `Tanggal Pesanan`, `Total Harga`), urutan kolom bebas, kolom tambahan diabaikan, dan file `.xlsx` bisa dibaca.
2. **Kalau ada yang tidak dikenal** (kolom, channel, atau status baru), aplikasi berhenti dan bertanya ke user lewat pilihan sederhana, tidak menebak diam-diam.
3. **AI hanya menyarankan jawabannya** (opsional, jika `GEMINI_API_KEY` diisi; tier gratis Gemini cukup). Yang dikirim ke AI hanya nama kolom dan label, **tidak pernah baris pesanan**. Saran AI ditandai "saran AI" dan tetap harus dikonfirmasi user. Jawaban AI di luar pilihan yang sah dibuang.
4. **Perhitungan tidak pernah memakai AI.** Semua angka dihitung pandas, jadi hasilnya pasti sama setiap kali dijalankan dan bisa dicek.

Tanpa API key, aplikasi tetap jalan normal; user cukup memilih sendiri.

## Tiga hal yang salah dikerjakan oleh AI

_(Draf dari kejadian nyata selama pengerjaan. Diperiksa ulang oleh saya sebelum dikirim.)_

1. **Catatan "cara penulisan channel" menghitung 3, padahal seharusnya 10.** AI menghitung variasi nama channel setelah nama-nama itu disatukan, jadi hasilnya selalu 3. Ketahuan saat membaca catatan di dashboard dan membandingkannya dengan pengecekan data di awal (10 variasi). Diperbaiki dengan menyimpan nama asli sebelum dirapikan, dan angkanya sekarang dikunci di test.
2. **Server sempat membuka seluruh folder ke browser, termasuk `data/` yang berisi data rahasia.** AI menyajikan seluruh folder proyek agar halaman bisa dibuka. Ketahuan saat meninjau kode servernya. Diperbaiki dengan hanya menyajikan halaman dan folder `src/`, lalu dicek: alamat file data memberi 404.
3. **Upload yang perlu dijawab dua kali tidak pernah tersimpan.** Saat file punya nama kolom yang tidak dikenal *dan* status baru, AI membuat aplikasi bertanya dalam dua layar terpisah. Jawaban layar pertama hilang saat layar kedua dikirim, sehingga aplikasi terus bertanya dan data minggu itu tidak tersimpan. Test otomatis tidak menangkapnya karena di test semua jawaban dikirim sekaligus. Ketahuan saat mencoba alur lengkap di browser dengan data dummy November: riwayat hanya mencatat 1 upload, dan upload minggu berikutnya mencatat "0 diperbarui". Diperbaiki: semua pertanyaan muncul di satu layar, jawaban digabung, dan test diperketat.

## Kalau ada lebih banyak waktu

- Upload tiga file ekspor asli dari tiap marketplace sekaligus, tanpa digabung manual (sumber duplikat).
- Login per orang (bukan satu kata sandi tim) dan catatan siapa yang mengupload.
- Memakai laporan penghasilan/settlement supaya terlihat uang yang benar-benar masuk setelah biaya admin dan voucher.
- Membandingkan dengan bulan sebelumnya.
- Unduh hasil ke Excel.

## Struktur kode

```
index.html              halaman
src/app.js              mengirim file ke API dan menggambar hasilnya (tanpa perhitungan)
src/style.css           tampilan (desktop dan HP)
biota/analysis.py       semua logika: baca file, rapikan, hitung, temuan (pandas)
biota/ai.py             saran AI opsional untuk kolom/label yang tidak dikenal
biota/storage.py        penyimpanan (SQLite di laptop, Postgres di live): gabung upload mingguan, ingat jawaban
api/index.py            API web (FastAPI): dashboard, daftar pesanan, upload, kata sandi tim
tests/test_analysis.py  test untuk setiap aturan + angka akhir + detail
tests/test_weekly.py    test upload mingguan, jawaban yang diingat, kata sandi, kasus tepi
tests/dummy_data.py     pembuat data dummy mingguan + jawaban benar yang dihitung terpisah
tests/test_dummy_weeks.py  dua ekspor dummy lewat API asli, dibandingkan dengan jawaban benar
tests/test_ai.py        AI tanpa internet (privasi, konfirmasi, coba ulang) + AI sungguhan (opsional)
vercel.json             pengaturan deploy Vercel
```

## Tools dan model AI

| Tool | Nama dan versi model AI | Dipakai untuk |
|---|---|---|
| Claude Code (CLI) | Claude Opus 5.5 (`claude-opus-5-5`) | Membaca brief, memeriksa data, menulis logika pandas, API, test, tampilan dan README |
| Google Gemini API (opsional, di dalam aplikasi) | Gemini 2.5 Flash | Menyarankan arti kolom/label yang tidak dikenal; tidak menghitung angka |
| Google Chrome | — | Mengecek dashboard di ukuran laptop dan HP |
| ElevenLabs | Eleven v4 (suara "Lyly") | Narasi video penjelasan |
| Playwright + ffmpeg | — | Merekam aplikasi asli dan menyusun video penjelasan |

## Log prompt

Lihat `prompt-log/`:

- `01-log-asli.txt`: ekspor apa adanya dari `/export` di Claude Code. Satu-satunya perubahan: API key yang sempat tertulis di percakapan diganti `[disamarkan]`.
- `02-prompt-best-practice.md`: **bukan log asli.** Ditulis ulang setelah pengerjaan: arahan yang sama, dibuat lebih lengkap dan teknis, untuk dipakai ulang. Prompt saya di log asli pendek karena saya bekerja dengan instruksi dan aturan yang sudah saya siapkan untuk AI sebelumnya; versi ini menuliskan konteks itu secara eksplisit.
