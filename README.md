# FB Reels RSS (versi cloud)

Versi `rss.py` tanpa tetingkap, berjalan di **GitHub Actions** setiap 2 jam.
RSS disimpan di **Cloudflare R2** dengan pautan awam, jadi PC tak perlu hidup.

Format RSS sama seperti sebelum ini (`<enclosure>` mp4 + blok "Kapsyen Penuh"), dengan tiga pembaikan:
- **mp4 kualiti tertinggi** yang ada audio (bukan format terakhir dalam senarai, yang kadang-kadang SD).
- **"See-mo.re" dibuang** dari hujung caption, dan item ditanda "(kapsyen terpotong)".
- **Item lama dikekalkan** (hingga `KEEP_ITEMS`). Kalau satu larian gagal, RSS lama tidak ditimpa dengan RSS kosong.

## Tujuan peringkat ini: uji sama ada Facebook benarkan

Facebook sering sekat scraping dari IP server. Selepas larian pertama, buka tab **Actions → larian → Summary**:

| Keputusan | Maksud |
|---|---|
| ✅ hijau, "Ada mp4" > 0 | Berjaya. Cara A boleh diteruskan. |
| ❌ merah, "Pautan reel dijumpai" = 0 | Halaman reels disekat. Lihat `reels_page.png` dalam artifact `debug-N`. |
| ❌ merah, pautan dijumpai tapi "Ada mp4" = 0 | Senarai berjaya, tapi yt-dlp disekat. Lihat "Ralat yt-dlp". |

Kalau disekat, ada dua pilihan (isi sebagai secret):
- `PROXY_URL`: proxy IP Malaysia, contoh `http://user:pass@host:port`.
- `FB_COOKIES`: kandungan fail cookies.txt dari akaun Facebook. **Guna akaun simpanan, bukan akaun utama.** Akaun yang dipakai dari IP server boleh kena checkpoint.

## Persediaan

### 1. Cloudflare R2
1. Buat bucket (atau guna bucket sedia ada dengan prefix `rss/`).
2. Bucket → Settings → **Public access**: hidupkan r2.dev, atau sambung domain sendiri.
3. R2 → Manage API tokens → token dengan kebenaran **Object Read & Write** untuk bucket tu.

### 2. GitHub
1. Push folder ini ke repo baru.
2. Settings → Secrets and variables → Actions:
   - **Secrets:** `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`
     (pilihan: `PROXY_URL`, `FB_COOKIES`)
   - **Variables:** `R2_PUBLIC_BASE_URL` (contoh `https://pub-xxxx.r2.dev`),
     `FB_PAGE_URLS` (senarai page, dipisah koma; lalai: penmerahdotcoms dan buletinmalaysiakuu), `MAX_REELS` (lalai 10)
3. Actions → **FB Reels RSS** → **Run workflow**.

Pautan RSS: satu bagi setiap page (`rss/penmerahdotcoms.xml`, `rss/buletinmalaysiakuu.xml`) dan satu gabungan semua page (`rss/semua.xml`).

## Minit GitHub Actions
Satu larian mengambil kira-kira 3–5 minit (pasang Chromium + tarik 10 reel).
- Repo **public**: minit percuma tanpa had. Secrets tetap selamat.
- Repo **private**: 2,000 minit percuma sebulan. Setiap 2 jam ≈ 1,800–2,700 minit, jadi mungkin terlebih. Setiap 3 jam lebih selamat.

## Uji di PC
```bash
pip install -r requirements.txt
python -m playwright install chromium
python fb_reels_rss.py          # tanpa R2: RSS disimpan di out/
python -m unittest discover -s tests
```

## Relay ke Postolab (`postolab_relay.py`)

Berjalan sebagai job kedua (`relay`) selepas RSS dijana, setiap 2 jam:
1. Baca `rss/semua.xml`, ambil reel yang belum diproses (maks `MAX_VIDEOS_PER_RUN`, lalai 5).
2. Semak video: teknikal, muzik (hanya jika `AUDD_API_TOKEN` ada), Gemini tonton video (visual + suara + teks).
3. **Hijau** → Gemini tulis caption berbeza bagi setiap Page (maks 7 perkataan + `...see-moo.re`, disemak oleh kod:
   tiada tuduhan, kaum/agama, ejekan, hashtag, emoji, pautan, dan tidak serupa caption lama)
   → upload mp4 ke R2 Postolab → `POST /api/external/videos` (slot :00/:30 seterusnya, profile `deli`).
   **Kuning** → `YELLOW_MODE` (`skip` lalai, atau `post`). **Merah** → tidak dipost.
4. Laporan: `R2_PUBLIC_BASE_URL/rss/laporan.html`.

### Tetapan tambahan
**Secrets:** `POSTOLAB_API_KEY` (kunci `pl_...`, sama seperti siakapkeli), `GEMINI_API_KEY`, pilihan `AUDD_API_TOKEN`.

**Variables:**
| Nama | Contoh | Maksud |
|---|---|---|
| `DRY_RUN` | `1` (lalai) | `1` = jana caption & laporan sahaja, tiada post. Tukar ke `0` bila puas hati. |
| `SOURCE_PAGE_MAP` | `penmerahdotcoms=111\|222,buletinmalaysiakuu=333` | ID Page dalam Postolab bagi setiap page sumber (video tidak dihantar balik ke page asalnya). |
| `EXCLUDE_PAGE_IDS` | `444,555` | Page Postolab yang tidak mahu terima video langsung. |
| `POSTOLAB_PROFILE` | `deli` | Profile dalam Postolab tempat queue dipaparkan. |
| `YELLOW_MODE` | `skip` | `skip` atau `post`. |
| `STAGGER` | `0` | `1` = Page ke-2 guna slot kosong ke-2, dst (elak video sama keluar serentak). |
| `POSTOLAB_URL` | `https://postolab.com` | Alamat Postolab. |

Reel yang dicuba dalam `DRY_RUN=1` masih akan diproses semula bila `DRY_RUN=0`.
