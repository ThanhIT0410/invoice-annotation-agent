# PIPELINE CHUẨN — CLAUDE-GUIDED TEXT GROUNDING & BBOX AUDIT (API)

**Phiên bản:** 2.0 — thay thế bản 1.0 (OCR-guided VLM). Quy tắc bắt buộc cho mọi lượt chạy.

**Mục tiêu:** Định vị chính xác từng giá trị trong biểu mẫu hóa đơn lấy từ invoice-review-api trên PDF gốc, gửi từng bbox lên backend, và báo cáo trung thực kết quả vào `logs.txt`.

**Nguyên tắc cốt lõi:** Claude chọn đúng chữ / đúng ngữ cảnh; OCR cung cấp hình học vùng chữ; kiểm tra trực quan crop phóng đại trên ảnh thẳng quyết định box có được chấp nhận hay không.

> **Quan trọng:** Đây là quy trình chuẩn bắt buộc cho các lần làm tiếp theo. Box chỉ được gọi là `verified` khi Claude đã tự xem crop của chính box đó. Coverage (số box đã gửi) không phải accuracy; không được gọi kết quả là ground truth chỉ dựa trên OCR, điểm khớp chuỗi, hay việc backend chấp nhận request.

---

## 1. Quy tắc không được vi phạm

1. **Bắt đầu từ API gốc mỗi lượt.** Tải lại PDF và biểu mẫu qua API; không dùng bbox cũ trên backend (`field_boxes`, `bbox.fields`) hay kết quả lượt trước làm đáp án hoặc nguồn tọa độ.
2. **Không sửa biểu mẫu.** Không gửi bất kỳ thay đổi giá trị nào lên backend. Giá trị trên biểu mẫu khác hóa đơn thật thì **chỉ báo cáo** trong `logs.txt`.
3. **Không cho Claude tự bịa tọa độ cuối.** Ước lượng bằng mắt của Claude chỉ là vùng ứng viên; tọa độ cuối phải đến từ hình học chữ (OCR trang / OCR vùng phóng to / lưới tọa độ) và được refine theo nét mực.
4. **Không cố gán đủ 100% bbox.** Chưa đủ bằng chứng thì để `needs_review` (không gửi gì) và nêu nguyên nhân. Chỉ gửi `box` cho trường `verified`; chỉ gửi `no_box` khi có lý do hợp lệ (Mục 3 – Bước 6). Không dùng `no_box` để "đóng" một trường chưa tìm kỹ.
5. **Không được gọi box là `verified`** nếu chưa xem crop của chính box đó, đối chiếu đủ ký tự, đúng dòng, đúng cột và đúng ngữ nghĩa.
6. **Không trộn hệ tọa độ.** Chỉ dùng đúng quy tắc xoay ở Mục 3 – Bước 1 và Bước 7; mọi phép biến đổi nằm trong `pipeline/geometry.py`, có phép nghịch đảo và đã được kiểm nghiệm.
7. **Không chỉ kiểm tra toàn trang thu nhỏ.** Bắt buộc xem crop phóng đại của từng field; xem lại crop của box sau mỗi lần sửa (tối đa 2 vòng sửa).
8. **Không đánh đồng coverage với accuracy.** `đã gửi / tổng target` không phải IoU, không phải precision.
9. **Giữ PDF nguyên bản.** Không sửa, không tái dựng PDF; chỉ render ra ảnh để làm việc. Ghi sha256 của PDF vào log.
10. **Không báo đã audit kỹ** nếu chỉ có điểm khớp chuỗi, tự kiểm tra hình học, hoặc nhìn vài ảnh mẫu.
11. **Quy tắc xoay là cố định.** Nếu áp dụng đúng quy tắc mà hóa đơn vẫn không thẳng → **dừng quy trình**, ghi lý do vào `logs.txt`, không thử góc khác, không gửi bbox.
12. **Bảo mật session.** `x-bbox-session` chỉ nằm trong `.env` / biến môi trường; Claude không đọc, không in ra.

---

## 2. Inputs và outputs

### Inputs

Mọi request đều gửi kèm header `x-bbox-session: <giá trị người dùng tự dán>`.

| API | Mục đích |
|---|---|
| `GET https://invoice-review-api.lehoangtung7629.workers.dev/api/documents/{doc_id}/pdf` | Tải PDF gốc |
| `GET https://invoice-review-api.lehoangtung7629.workers.dev/api/documents/{doc_id}` | Lấy biểu mẫu hóa đơn đã điền sẵn |

Trong biểu mẫu, dữ liệu nằm ở `document.invoice_json.invoices[i]`. Mỗi trường dưới đây là **một target** và phải được giải quyết bằng đúng một trong hai action: `box` (gửi tọa độ) hoặc `no_box`. Trường có giá trị null trong biểu mẫu cũng là target (luôn `no_box`).

| Nhóm | field_key gửi lên | Khóa trong biểu mẫu | Nhãn | Màu |
|---|---|---|---|---|
| Hóa đơn | `invoices[i].supplier.supplier_name` | `supplier_name` | Tên nhà cung cấp | `#dc2626` |
| Hóa đơn | `invoices[i].supplier.tax_code` | `supplier_tax_code` | MST bên bán | `#e11d48` |
| Hóa đơn | `invoices[i].invoice.invoice_number` | `invoice_number` | Số hóa đơn | `#7c3aed` |
| Hóa đơn | `invoices[i].invoice.invoice_serial` | `invoice_serial` | Ký hiệu | `#0891b2` |
| Hóa đơn | `invoices[i].invoice.invoice_date` | `invoice_date` | Ngày hóa đơn | `#be185d` |
| Hóa đơn | `invoices[i].invoice.vat_rate_percent` | `vat_rate_percent` | VAT | `#2563eb` |
| Hàng | `invoices[i].items[j].medicine_name_strength_manufacturer_country` | cùng tên | Tên thuốc / hàm lượng / hãng / nước | `#c2410c` |
| Hàng | `invoices[i].items[j].unit` | `unit` | Đơn vị | `#a21caf` |
| Hàng | `invoices[i].items[j].quantity` | `quantity` | Số lượng | `#0284c7` |
| Hàng | `invoices[i].items[j].unit_price` | `unit_price` | Đơn giá | `#0d9488` |
| Hàng | `invoices[i].items[j].line_amount` | `line_amount` | Thành tiền | `#a16207` |
| Hàng | `invoices[i].items[j].vat_amount` | `vat_amount` | Tiền VAT | `#4338ca` |
| Hàng | `invoices[i].items[j].lot_number` | `lot_number` | Số lô | `#475569` |
| Hàng | `invoices[i].items[j].expiry_date` | `expiry_date` | Hạn dùng | `#b45309` |
| Tổng | `invoices[i].totals.grand_total` | `grand_total` | Tổng giá trị hóa đơn | `#4d7c0f` |

Số target cần đạt tham chiếu: `bbox.required_count + bbox.total_supplement.required_count` của backend.

> Mẫu `invoices[i].items[j].<key>` cho trường hàng được suy ra từ mẫu `invoices[0].totals.grand_total`; nếu backend dùng mẫu khác, sửa `ITEM_KEY_TEMPLATE` trong `pipeline/config.py`.

### Outputs

**Lên backend** — mỗi target một request, gồm hai loại action:

```
PUT https://invoice-review-api.lehoangtung7629.workers.dev/api/bbox/documents/{doc_id}
```

```json
{
  "field_key": "invoices[0].invoice.invoice_number",
  "action": "box",
  "box": {
    "fieldKey": "invoices[0].invoice.invoice_number",
    "label": "Số hóa đơn",
    "color": "#7c3aed",
    "pageNumber": 1,
    "points": [
      { "x": 0.336, "y": 0.816 },
      { "x": 0.391, "y": 0.816 },
      { "x": 0.391, "y": 0.838 },
      { "x": 0.336, "y": 0.838 }
    ]
  }
}
```

Action `box`: tọa độ chuẩn hóa 0–1 trong hệ backend, 4 góc theo thứ tự trên-trái, trên-phải, dưới-phải, dưới-trái; `pageNumber` đếm từ 1.

Action `no_box` — trường không có vùng nào để đánh dấu:

```json
{
  "field_key": "invoices[0].invoice.vat_rate_percent",
  "action": "no_box"
}
```

Dùng `no_box` trong đúng ba trường hợp (lý do ghi trong `no_box_reason`, chỉ lưu cục bộ và báo cáo trong log):

| `no_box_reason` | Khi nào |
|---|---|
| `null_value` | Biểu mẫu để null |
| `not_found` | Đã tìm kỹ (ảnh thẳng, `zoom`, `ocr-region`) mà trường không có trên hóa đơn |
| `zero_other_form` | Giá trị bằng 0 nhưng hóa đơn in dạng khác, ví dụ `KCT` (không chịu thuế), `KKKNT`, `x`, `-`, `\` |

> Body `no_box` được suy ra từ body `box`. Nếu backend cần thêm trường, chỉ sửa hàm `no_box_payload()` trong `pipeline/config.py`.

**`logs.txt`** (append, không ghi đè) — mỗi hóa đơn một khối gồm đủ 4 mục:

1. Đã hoàn thành tất cả bbox chưa (đã gửi / tổng target, tách `box` và `no_box`, lý do của từng `no_box`, trạng thái backend sau khi gửi, danh sách còn thiếu và lý do).
2. Giá trị biểu mẫu có sai lệch so với hóa đơn thật không (chỉ báo cáo, không sửa).
3. Orientation từng trang, có vấn đề gì không.
4. Chất lượng từng bbox (padding quá rộng / quá hẹp, cắt lẹm nội dung khác, cắt lẹm chính nội dung, dính đường kẻ, chồng lấn…).

**Thư mục làm việc `work/<doc_id>/`** (bằng chứng truy vết):

| File | Nội dung |
|---|---|
| `original.pdf`, `document.json`, `meta.json` | PDF gốc (giữ byte), biểu mẫu gốc, sha256 + thông tin trang |
| `pages/page_XXX_raw.png`, `page_XXX_upright.png` | Ảnh gốc (trang PDF như khi nhận) và ảnh thẳng (sau khi xoay) |
| `orientation.json` | Orientation từng trang + kích thước ảnh gốc và ảnh thẳng |
| `targets.json`, `words/`, `candidates.json` | Danh sách target, hình học chữ, ứng viên |
| `boxes_upright.json` | Quyết định cuối cho từng target: box (pixel trên ảnh thẳng) hoặc `no_box` + lý do, status, method, note (Claude viết) |
| `refine_log.json` | before/after của mọi lần bó box |
| `auto_check.json`, `review/round_N/`, `review_state.json` | Kết quả tự kiểm tra; sheet crop và ảnh phủ box của từng vòng review; box nào đã được xem ở vòng nào |
| `payloads.json` | Request sẽ gửi (`box` và `no_box`) |
| `upload_result.json`, `remote_after.json` | Phản hồi từng PUT, trạng thái backend sau khi gửi |
| `audit.json` | Đánh giá của Claude: orientation, sai lệch, review từng box |
| `DONE` / `ABORTED` | Marker kết thúc (run_batch dùng để biết phiên đã báo cáo) |

---

## 3. Pipeline bắt buộc — theo thứ tự

Mọi bước gọi qua `python -m pipeline.agent <lệnh> <doc_id>`.

### Bước 0 — Fresh start / loại ảnh hưởng cache

- Tải PDF và biểu mẫu từ API vào thư mục công việc mới `work/<doc_id>/`; thư mục của lượt trước bị xóa trước khi tải.
- Không đọc/sao chép bbox đang có trên backend (`field_boxes`, `bbox.fields`), `boxes_upright.json`, ảnh review hay kết quả lượt trước làm ground truth.
- Ghi checksum của PDF và biểu mẫu; kết quả OCR chỉ gắn với ảnh thẳng của chính lượt chạy hiện tại, không dùng lại giữa các lượt.
- Không xóa `logs.txt`, không xóa hay sửa dữ liệu trên backend.

**Chi tiết kỹ thuật**

- Thực hiện trong lệnh `start <doc_id>` (hàm `fetch`).
- Gọi `GET /api/documents/{doc_id}` và `GET /api/documents/{doc_id}/pdf`, header `x-bbox-session` đọc từ biến môi trường `X_BBOX_SESSION` hoặc file `.env`.
- Thử lại tối đa 3 lần khi lỗi mạng hoặc HTTP 408/429/5xx (chờ 2 rồi 4 giây); lỗi 4xx khác dừng ngay. File tải về phải bắt đầu bằng `%PDF`.
- API lỗi → tự ghi khối `abort` vào `logs.txt` và dừng.
- Lưu `original.pdf` (nguyên byte), `document.json`, `meta.json` (`pdf_sha256`, `document_sha256`, `pdf_bytes`, thời điểm tải).

### Bước 1 — Render PDF và xác nhận orientation từng trang

1. Mở PDF gốc; đọc kích thước trang và cờ `/Rotate`, nhưng không mặc định cờ xoay phản ánh chiều chữ cần đọc.
2. Render từng trang thành **ảnh gốc** ở DPI cố định và lưu kích thước ảnh gốc.
3. Claude xem **thumbnail** của ảnh gốc và quyết định dựa trên **hướng đọc của chữ viết** (không dựa trên tỷ lệ khung trang, không dựa trên cờ `/Rotate`, không dựa trên OSD):
   - Chữ chạy dọc, hóa đơn nằm nghiêng → **landscape**.
   - Chữ đã đọc thẳng trái → phải, trên → dưới → **portrait**.
4. Tạo **ảnh thẳng** bằng phép xoay xác định theo quy tắc cố định; lưu orientation, kích thước ảnh gốc và ảnh thẳng.

| Orientation | Ảnh gốc (khi nhận) | Ảnh thẳng | Bbox khi gửi backend |
|---|---|---|---|
| **landscape** | nằm nghiêng | xoay ảnh **90° ngược chiều kim đồng hồ** | quay bbox **180°** |
| **portrait** | đã thẳng | **không xoay** | quay bbox **90° cùng chiều kim đồng hồ** |

5. Claude xem thumbnail của ảnh thẳng và **xác nhận chữ đã thẳng**. Nếu áp dụng đúng quy tắc mà hóa đơn vẫn không thẳng (ví dụ chữ lộn ngược 180°, hoặc trang nghiêng theo chiều ngược lại nên xoay ngược chiều kim đồng hồ làm chữ lộn ngược) → **dừng quy trình**, ghi vào `logs.txt` hướng chữ quan sát được. Không thử góc khác, không gửi bbox.
6. Nếu PDF có nhiều trang, xử lý và ghi orientation riêng cho từng trang.
7. Ảnh dùng cho OCR, cho Claude và cho ảnh phủ box phải cùng là ảnh thẳng; mọi quy đổi khác đi qua phép biến đổi đã ghi.

> **Lưu ý cực quan trọng:** cờ `/Rotate` chỉ là thông tin tham khảo (được ghi vào log). Quyết định orientation luôn dựa trên chữ nhìn thấy trên ảnh gốc. Xoay sai 90°/180° gây lỗi hàng loạt.

**Chi tiết kỹ thuật**

- `start <doc_id>` (hàm `render_pages`, chạy ngay sau `fetch`) → `pages/page_XXX_raw.png` và thumbnail `page_XXX_raw_thumb.png`; Claude đọc thumbnail; `rotate <doc_id> landscape|portrait [số trang...]` (mặc định mọi trang) → `pages/page_XXX_upright.png` và `page_XXX_upright_thumb.png`; Claude đọc thumbnail ảnh thẳng; nếu không thẳng: `abort <doc_id> "<lý do>"`.
- Thumbnail có cạnh dài 800 pixel (~600 token so với ~4.800 token của ảnh 200 DPI) — đủ để thấy hướng chữ. Thumbnail chỉ để nhìn; mọi tọa độ vẫn tính trên ảnh đầy đủ.
- Render bằng PyMuPDF ở 200 DPI (`RENDER_DPI`), có áp cờ `/Rotate` như trình xem PDF (`RESPECT_PDF_ROTATE = True`; đổi `False` nếu backend hiển thị trang bỏ qua cờ này).
- Xoay ngược chiều kim đồng hồ bằng `Image.transpose(ROTATE_90)` của Pillow — phép xoay chính xác theo pixel, không nội suy, không mất nét.
- `orientation.json` lưu cho từng trang: `orientation`, `raw_width`, `raw_height`, `upright_width`, `upright_height`, `pdf_rotate`, đường dẫn ảnh thẳng.

### Bước 2 — Đọc biểu mẫu, lập target manifest

- Giữ nguyên từng giá trị nguồn, tên trường, chỉ số hóa đơn, chỉ số dòng hàng và trang gợi ý (`page_number`).
- Mọi trường trong danh sách field (Mục 2) là một target, **kể cả trường null** — trường null sẽ được giải quyết bằng `no_box`.
- Không dùng OCR để đổi nội dung mục tiêu, kể cả khác dấu, khác số hoặc cách viết.
- Lập danh sách `field_key` duy nhất để phân biệt các trường có giá trị trùng nhau và nhiều hàng thuốc.

**Chi tiết kỹ thuật**

- Hàm `manifest` → `targets.json`. Bước 2–5 và vòng review đầu của Bước 6 là các bước riêng (hàm `manifest`, `candidates`, `draft`, `refine`, `review`), nhưng chạy liền bằng một lệnh `analyze <doc_id>` vì giữa chúng không có quyết định nào của Claude — giảm số lượt agent.
- Đọc `document.invoice_json.invoices[i]` (chấp nhận cả trường hợp `invoice_json` là chuỗi JSON). `grand_total` lấy ở cấp hóa đơn, nếu không có thì tìm trong `totals`.
- `field_key`: `invoices[i].<đường dẫn>` cho trường hóa đơn/tổng; `invoices[i].items[j].<khóa>` cho trường hàng (`INVOICE_KEY_TEMPLATE`, `ITEM_KEY_TEMPLATE` trong `config.py`).
- Mỗi target lưu: `field_key`, nhóm (`invoice`/`item`/`total`), chỉ số hóa đơn, chỉ số dòng, khóa, nhãn, màu, giá trị nguyên bản, cờ `form_null`, trang gợi ý.
- Số target backend yêu cầu = `bbox.required_count + bbox.total_supplement.required_count`, lưu vào `meta.json` để đối chiếu trong báo cáo.

### Bước 3 — OCR tạo ứng viên hình học

- Chạy OCR trên ảnh thẳng với bbox mức word: text, x1, y1, x2, y2, confidence, trang.
- Với chữ nhỏ, tên thuốc dài, bảng nhiều cột hoặc OCR hỏng: crop vùng nghi vấn, phóng đại 2–5 lần, thử OCR lại ở crop; lưu gốc tọa độ và hệ số phóng đại để quy đổi đúng về pixel ảnh thẳng.
- Chuẩn hóa text chỉ phục vụ so khớp: Unicode, khoảng trắng, dấu phân cách số, dấu tiếng Việt (có kiểm soát), các cách viết số/ngày. Không ghi đè giá trị nguồn.
- Ghép các token OCR nếu target bị tách thành nhiều từ/nhiều dòng; chỉ ghép các token thật sự thuộc target.
- Không mặc định box cấp ô bảng / cả hàng là bbox của riêng giá trị.
- Trường null không cần tìm ứng viên.

**Chi tiết kỹ thuật**

- Hàm `candidates` → `words/words_pXXX.json`, `candidates.json`; hàm `draft` → `boxes_upright.json` nháp (cả hai nằm trong `analyze`); lệnh `ocr-region <doc_id> <trang> x1,y1,x2,y2` để OCR lại một vùng.
- Hóa đơn là PDF scan nên không dùng lớp chữ PDF. OCR: Tesseract `vie+eng`, chế độ phân tích bố cục tự động (`--psm 3`) cho cả trang; `ocr-region` phóng đại 3 lần và dùng chế độ một khối chữ (`--psm 6`), tọa độ quy về ảnh thẳng theo `x_gốc = x_crop / hệ_số + gốc_x`.
- Gom dòng: word có tâm theo trục dọc lệch nhau không quá nửa chiều cao trung vị thì cùng dòng; sắp trái → phải.
- Chuẩn hóa so khớp: tách dấu theo Unicode rồi bỏ dấu, `đ` → `d`, chữ thường, bỏ khoảng trắng.
- Biến thể giá trị: số (`15000` ↔ `15.000`, `15,000`, `15.000,00`, `15,000.00`…), ngày (`2026-03-26` ↔ `26/03/2026`, `26-3-26`, `Ngày 26 tháng 03 năm 2026`, `03/2026`…), phần trăm (`5` ↔ `5%`).
- Ghép token kiểu ô bảng: đi hết đoạn liền mạch trên dòng (dừng khi khoảng trống > 1,5 lần chiều cao chữ — ranh giới cột), rồi xuống tối đa 2 dòng kế, chỉ lấy word nằm dưới cùng cột và cách dòng trên không quá 1,5 lần chiều cao chữ.
- Chấm điểm bằng độ giống chuỗi (difflib); giữ ứng viên có điểm ≥ 0,8; khử trùng ứng viên chồng nhau (tỷ lệ giao trên hợp > 0,5); giữ tối đa 5 ứng viên mỗi target, kèm `segments` theo từng dòng nếu giá trị nhiều dòng.
- Giá trị nằm trong token dài hơn (`Số:00064098`) → ứng viên `partial_token` với box con ước lượng theo vị trí ký tự, bắt buộc refine. Với trường số/ngày, chỉ chấp nhận khi không dính liền chữ số hay dấu phân cách khác (tránh `0.00` khớp vào giữa `99.600.000`).
- Neo theo hàng: với trường hàng có nhiều ứng viên tốt (vd `VIÊN` xuất hiện ở mọi dòng), ưu tiên ứng viên cùng trang và gần tâm dọc nhất của dòng đó — tâm dòng lấy trung vị từ các trường cùng dòng chỉ có một ứng viên tốt (thường là thành tiền, tên thuốc).
- `draft`: ứng viên tốt nhất (sau khi neo theo hàng) có điểm ≥ 0,9 → box `needs_review`; trường null → `no_box` (`null_value`); không có ứng viên → `needs_review` không box (không tự thành `no_box`).

### Bước 4 — Claude semantic grounding / chọn đúng occurrence

Claude xem ảnh thẳng hoặc crop cùng target list và tập ứng viên OCR để trả lời:

- Giá trị đang nằm ở đâu trong layout, thuộc trường nào và hàng nào?
- Nếu số xuất hiện nhiều lần, đâu là tiền hàng, tiền thuế, tổng thanh toán (`grand_total` = tổng cuối đã gồm VAT)?
- Nếu nhiều hàng thuốc giống nhau, số lượng / đơn giá / thành tiền thuộc đúng hàng nào?
- `vat_rate_percent` là thuế suất (`5%`), không phải tiền thuế.
- Các trường ngày: dạng chữ "Ngày 1 tháng 2 năm 2000" → box cả cụm (kể cả chữ "Ngày"); dạng số "Ngày: 1/2/2000" → chỉ box "1/2/2000", không lấy nhãn "Ngày:".
- Target có đủ chữ trên trang này hay chỉ xuất hiện một phần? Nếu là hóa đơn nhiều trang, có ở trang kế tiếp không?
- Giá trị thật trên hóa đơn có khớp biểu mẫu không? Khác định dạng không tính là sai lệch. Khác nội dung → ghi `mismatches`, box đặt vào vị trí giá trị **thật** của trường, không sửa biểu mẫu.
- Trường không có vùng nào để box: biểu mẫu null; biểu mẫu = 0 nhưng hóa đơn in `KCT`, `KKKNT`, `x`, `-`… (in đúng `0` / `0%` thì vẫn box); hoặc đã tìm kỹ mà không có → `no_box` kèm lý do.

Claude có thể ước lượng vùng thô khi OCR không thấy, nhưng không được coi vị trí thô là tọa độ cuối. Phải OCR lại vùng đó, refine và kiểm tra tiếp.

**Chi tiết kỹ thuật**

- Claude chỉ mở ảnh trang khi cần, bằng `zoom <doc_id> <trang> x1,y1,x2,y2` → ảnh phóng to 3 lần kèm lưới tọa độ (vạch mỗi 50 pixel, nhãn theo pixel ảnh thẳng) để Claude đọc vị trí; tiếp theo `ocr-region` trên vùng đó để lấy tọa độ chữ chính xác.
- Ứng viên khác của một target: tìm `field_key` trong `candidates.json` (không đọc cả file để tiết kiệm token).
- Claude ghi quyết định bằng lệnh, gọi target theo số thứ tự in trên sheet (`#3`), gộp nhiều target trong một lần gọi: `mark <doc_id> verified|needs_review <số...|all> [--note ...]`, `mark <doc_id> no_box <lý do> <số...> [--note ...]`; `setbox <doc_id> <số> <trang> x1,y1,x2,y2 [--note ...]` để đặt box tay (tự refine theo nét chữ rồi khóa bằng `"locked": true`).
- Mỗi phần tử của `boxes_upright.json`: `field_key`, `page`, `bbox` (`[x1, y1, x2, y2]` pixel ảnh thẳng, hoặc `null`), `status`, `no_box_reason` (chỉ khi `no_box`), `method`, `note`.
- `no_box_reason` hợp lệ: `null_value`, `not_found`, `zero_other_form`.

### Bước 5 — Refine bbox theo hình học chữ

- Tạo box nhỏ nhất bao trọn toàn bộ ký tự của target, padding tối thiểu phù hợp độ phân giải (thường khoảng 2–3 pixel, không máy móc).
- Đảm bảo không ăn vào nhãn, dòng bên cạnh, đường kẻ khung bảng hoặc token không thuộc target.
- Với giá trị nhiều dòng: lưu các segment khi cần và dùng hình chữ nhật bao ngoài cho định dạng đích (backend nhận một hình chữ nhật 4 điểm).
- Phân biệt các số có cùng text ở cột đơn giá và thành tiền; không sao chép cùng box sang hai field chỉ để hoàn thành.
- Mọi phép điều chỉnh phải lưu box trước, box sau, lý do và bằng chứng.

**Chi tiết kỹ thuật**

- Hàm `refine` (nằm trong `analyze`) → cập nhật `boxes_upright.json`, ghi `refine_log.json`. Box đặt bằng `setbox` được refine ngay khi đặt.
- Thuật toán: cắt vùng box kèm lề ngữ cảnh `max(8, 0,6 × chiều cao box)` pixel → tách mực bằng ngưỡng Otsu (không vượt mức xám 190; vùng gần đồng màu coi là không có mực) → bỏ đường kẻ bảng (hàng có mực ≥ 60% bề rộng vùng khi vùng rộng ≥ 60 pixel; cột có mực ≥ 90% chiều cao vùng khi vùng cao ≥ 30 pixel) → nếu nét chữ vắt qua cạnh box thì nới cạnh đó từng pixel cho tới khi trọn nét (tối đa bằng lề ngữ cảnh) → bó sát nét mực + padding `PAD_PX` = 3 pixel.
- Box gốc được giữ trong `before_bbox`; mỗi lần thay đổi ghi `before`, `after`, số pixel đã nới từng cạnh và thời điểm.

### Bước 6 — Crop-by-crop visual verification (bắt buộc)

Với từng bbox, không chỉ với ảnh mẫu:

1. Render crop bám bbox có thêm ngữ cảnh xung quanh, phóng đại đủ để soi rõ từng nét chữ.
2. Kiểm tra: đủ ký tự, đúng dấu, không cắt đầu/cuối, không lấn sang giá trị khác, đúng hàng, đúng cột, đúng occurrence; padding không quá rộng, không quá hẹp; các box tách rời hoặc nằm trọn trong nhau, không giao cắt một phần; padding trên/dưới và trái/phải cân đối — chỉ chấp nhận ngoại lệ khi bất khả kháng và ghi lý do.
3. Nếu sai: sửa box, render crop lại, kiểm tra lại cho tới khi đạt — **tối đa 2 vòng sửa**. Hết 2 vòng mà box vẫn chưa đạt → `needs_review` kèm lý do, không sửa tiếp.
4. Đối chiếu box với ảnh thẳng toàn trang để phát hiện lệch hệ tọa độ và box trúng hàng khác.
5. Mỗi field phải có trạng thái riêng:
   - `verified`: đã xem crop và chấp nhận đầy đủ nội dung / hình học / ngữ nghĩa → gửi `box`;
   - `no_box`: `bbox = null`, bắt buộc kèm lý do `null_value` / `not_found` / `zero_other_form` → gửi `no_box`;
   - `needs_review`: có ứng viên nhưng chưa đủ bằng chứng, hoặc chưa tìm kỹ → không gửi gì.
6. Nếu việc xem crop chưa được thực hiện thật, tuyệt đối không ghi `verified`. Chỉ kết luận `no_box` (`not_found`) sau khi đã xem ảnh thẳng, phóng to vùng có nhãn/cột tương ứng và OCR lại vùng đó.

**Chi tiết kỹ thuật**

- Lệnh: `review <doc_id>` (vòng 1 nằm trong `analyze`) → `auto_check.json`, `review/round_N/sheet_XX.png`, `review/round_N/overlay_page_XXX.png`. Claude phải đọc **mọi** ảnh mà lệnh in ra.
- **Chỉ xem lại box đã sửa:** vòng 1 render crop cho mọi box; các vòng sau chỉ render crop cho box mới hoặc có tọa độ khác lần đã xem (`review_state.json` lưu box nào đã được xem với tọa độ nào).
- **Giới hạn vòng:** tối đa 3 vòng review (1 vòng đầu + 2 vòng sửa, `MAX_REVIEW_ROUNDS`); lệnh từ chối vòng thứ 4.
- **Chốt chặn:** `mark ... verified` và `convert` từ chối box có tọa độ chưa được xem crop sau lần sửa cuối.
- **Phóng đại theo chiều cao chữ** thay vì hệ số cố định: hệ số = 48 pixel ÷ chiều cao dòng chữ, kẹp trong khoảng 2–5 lần; crop quá dài (tên công ty) được giảm tới 1,5 lần để vừa sheet. Phóng đại cố định với chữ vốn đã to chỉ tốn thêm token mà không thêm thông tin.
- Crop: lề ngữ cảnh `max(12, 0,6 × chiều cao box)` pixel; vẽ box theo màu của trường; tiêu đề 2 dòng ghi số thứ tự, nhãn, trang, giá trị biểu mẫu, kết quả tự kiểm tra.
- **Xếp sheet dưới trần token:** Claude tính ảnh theo ô 28×28 pixel (tối đa 4.784 token, ảnh lớn hơn bị thu nhỏ). Crop được xếp theo hàng vào sheet tối đa 1.904 × 1.960 pixel (≤ 4.760 token) và cắt sát nội dung — không bị thu nhỏ, không trả token cho nền trống. Hóa đơn 15 trường thường gọn trong 1 sheet.
- **Ảnh phủ box toàn trang:** thu nhỏ còn cạnh dài 1.300 pixel (~1.500 token), chỉ tạo ở vòng 1 hoặc khi box mới / bị dời xa hơn chiều cao của chính nó (để phát hiện nhầm hàng); box chỉ nới/bó vài pixel thì crop là đủ.
- Máy tự kiểm tra từng box (chỉ là gợi ý, Claude quyết định):

| Mã | Ý nghĩa | Điều kiện |
|---|---|---|
| `cut_<cạnh>` | Cắt lẹm — vào chính nội dung hoặc nội dung lân cận | Có ít nhất 2 pixel mực nằm ngay hai bên mép box tại cùng vị trí |
| `pad_wide_<cạnh>` | Padding quá rộng | Khoảng cách từ nét chữ tới mép lớn hơn `max(3 × PAD_PX, 0,5 × chiều cao dòng chữ)` |
| `pad_narrow_<cạnh>` | Padding quá hẹp | Nét chữ chạm mép box (khoảng cách < 1 pixel) mà không bị cắt |
| `near_<trái/phải>` | Có thể cắt thiếu ký tự (box dừng ở khe giữa hai ký tự) hoặc sát nội dung lân cận | Có nét chữ ngoài cạnh trái/phải, trong dải chiều cao chữ, cách mép < 0,3 lần chiều cao chữ (hẹp hơn khoảng trắng giữa hai từ) |
| `inner_gap` | Có thể chứa thêm nhãn / giá trị khác | Khoảng trống ngang trong box lớn hơn 1,5 lần chiều cao dòng chữ |
| `table_line` | Dính đường kẻ bảng | Đường kẻ ngang/dọc đã phát hiện nằm trong box |
| `overlap` / `duplicate` | Chồng lấn / trùng box của field khác | Giao nhau > 0; trùng khi tỷ lệ giao trên hợp > 0,9 |
| `empty` | Box rỗng | Không có nét mực nào trong box |

- Claude chỉ ghi vào `audit.json` → `box_reviews` các box đánh giá có vấn đề (`verdict`: `issue`, danh sách lỗi, ghi chú); mục 4 của `logs.txt` chỉ liệt kê các box này, kèm kết quả tự kiểm tra. Không có box nào có vấn đề thì mục 4 ghi một dòng "không có box nào có vấn đề".

### Bước 7 — Quy đổi sang hệ tọa độ backend

Tọa độ chuẩn hóa trên ảnh thẳng: `x = pixel_x / chiều_rộng_ảnh_thẳng`, `y = pixel_y / chiều_cao_ảnh_thẳng`.

| Orientation | Ảnh gốc → ảnh thẳng (pixel) | Ảnh thẳng (chuẩn hóa) → backend | Backend → ảnh thẳng (nghịch đảo) |
|---|---|---|---|
| landscape | `(x, y) → (y, chiều_rộng_ảnh_gốc − x)` (xoay 90° ngược chiều kim đồng hồ) | `(x, y) → (1 − x, 1 − y)` (quay 180°) | `(x, y) → (1 − x, 1 − y)` |
| portrait | `(x, y) → (x, y)` | `(x, y) → (1 − y, x)` (quay 90° cùng chiều kim đồng hồ) | `(x, y) → (y, 1 − x)` |

- Biến đổi 4 góc, lấy min/max, kẹp trong [0, 1], làm tròn 4 chữ số, xuất theo thứ tự trên-trái, trên-phải, dưới-phải, dưới-trái.
- Hàm `convert` (trong `finish`): quy đổi box `verified` và thêm request `no_box` cho các trường `no_box`; dừng nếu còn target chưa có trong `boxes_upright.json`, hoặc còn box `verified` chưa được xem crop sau lần sửa cuối.
- Quy tắc xoay đã được kiểm nghiệm trước, nên pipeline không kiểm tra khứ hồi cho từng hóa đơn (tiết kiệm token). Trong cả hai trường hợp, phép tổng hợp ảnh gốc → backend đều tương đương "xoay ảnh gốc 90° cùng chiều kim đồng hồ".

> **Cảnh báo:** Không sửa công thức trong `geometry.py` bằng tay cho từng hóa đơn. Nếu thay đổi quy tắc, sửa ở một chỗ và kiểm nghiệm lại trên hóa đơn thật.

> **Vận hành:** Bước 7, 8 (phần gửi) và 9 là ba bước riêng (`convert`, `upload`, `report`). Giữa chúng không có quyết định nào của Claude, nên khi vận hành chạy liền bằng một lệnh `finish <doc_id>`, sau khi Claude đã viết `audit.json` (nội dung audit không phụ thuộc kết quả quy đổi). `finish` dừng ở bước nào lỗi và báo lỗi bước đó.

### Bước 8 — Kiểm tra chất lượng đầy đủ và gửi bbox

1. Claude viết `audit.json` (trước khi chạy `finish`): orientation từng trang (`upright_ok`, ghi chú), `mismatches`, `box_reviews` cho **các box có vấn đề** (`verdict`: `issue`, danh sách lỗi, ghi chú).
2. Kiểm tra tự động trước khi gửi: JSON hợp lệ, field_key thuộc target, status hợp lệ, bbox hữu hạn và nằm trong ảnh, x1<x2, y1<y2; `no_box` không có bbox và có lý do hợp lệ; trường null bắt buộc là `no_box`;
3. Hàm `upload` (trong `finish`): PUT từng target (một request cho mỗi `box` hoặc `no_box`), retry khi lỗi mạng / 429 / 5xx, lưu phản hồi từng request; sau đó GET lại biểu mẫu để ghi trạng thái backend (`resolved_count / required_count`).
4. Báo cáo tách riêng: target / đã gửi `box` / đã gửi `no_box` (theo lý do) / còn `needs_review` / gửi lỗi. Không gọi tỷ lệ đã gửi là accuracy.

### Bước 9 — Báo cáo vào logs.txt

- Hàm `report` (trong `finish`): append một khối vào `logs.txt` gồm đủ 4 mục (Mục 2 – Outputs), sha256 PDF, engine OCR đã dùng, và ghi chú của Claude; tạo marker `DONE`.
- Lệnh `abort <doc_id> "<lý do>"`: khối báo cáo dừng sớm có lý do; tạo marker `ABORTED`.
- Ví dụ khối log:

```
======================== 2026-11-02 04:18:00 +0700 | doc_id=202606241434 ========================
KẾT QUẢ CHUNG: HOÀN THÀNH
PDF sha256=2ca94524f875929c… | 1 trang | nguồn hình học: ocr:tesseract:vie+eng
1) BBOX: đã gửi thành công 15/15 target (13 box, 2 no_box) (backend yêu cầu 15)
   Backend sau khi gửi: status=done, resolved 14/14, box_count=13; tổng tiền: 1/1 (done)
   - no_box invoices[0].invoice.vat_rate_percent [VAT]: giá trị bằng 0 nhưng in dạng khác (vd KCT = không chịu thuế) (đã gửi) — hóa đơn in "KCT"
   - no_box invoices[0].items[0].lot_number [Số lô]: biểu mẫu để null (đã gửi)
2) SAI LỆCH BIỂU MẪU vs HÓA ĐƠN: CÓ 1 trường (chỉ báo cáo, KHÔNG sửa)
   - invoices[0].items[0].quantity: biểu mẫu=15000 | hóa đơn='1.500' (trang 1)
3) ORIENTATION:
   - Trang 1: landscape (xoay ảnh 90° ngược chiều kim đồng hồ để thẳng; bbox quay 180° khi gửi); /Rotate PDF=90; đã thẳng (Claude xác nhận)
4) CHẤT LƯỢNG TỪNG BBOX:
   - invoices[0].items[0].expiry_date [Hạn dùng]: status=verified | Claude: issue | lỗi: chữ số cuối chạm đường kẻ dọc | tự kiểm tra: box dính đường kẻ bảng dọc
   - invoices[0].invoice.vat_rate_percent [VAT]: no_box — giá trị bằng 0 nhưng in dạng khác (vd KCT = không chịu thuế)
```

---

## 4. Quy tắc quyết định box

| Tình huống | Xử lý bắt buộc |
|---|---|
| OCR và Claude cùng xác định đúng text/field; crop đầy đủ | Refine theo nét mực, kiểm tra crop, có thể `verified` |
| Text đúng nhưng xuất hiện nhiều lần | Claude chọn occurrence dựa vào nhãn, hàng, cột; kiểm tra crop |
| Không có ứng viên | `zoom` định vị → `ocr-region` lấy tọa độ → refine → review |
| Ứng viên chỉ chứa một phần target | Ghép token / sửa theo ảnh, refine, kiểm tra đủ chữ |
| Ứng viên `partial_token` (giá trị dính nhãn trong một token) | Refine bắt buộc; kiểm tra box không ăn vào nhãn |
| Ứng viên và quan sát của Claude mâu thuẫn | Không tự chọn theo điểm khớp; xem ảnh/crop, ghi vào audit |
| Giá trị trên hóa đơn khác biểu mẫu | Box vị trí giá trị thật của trường; ghi `mismatches`; không sửa biểu mẫu |
| Biểu mẫu để null | `no_box` (`null_value`); nếu hóa đơn có in giá trị thì ghi thêm `mismatches` |
| Biểu mẫu = 0, hóa đơn in `KCT` / `x` / `-`… | `no_box` (`zero_other_form`) |
| Biểu mẫu = 0, hóa đơn in `0` / `0%` | Box như bình thường |
| Đã tìm kỹ mà trường không có trên hóa đơn | `no_box` (`not_found`), ghi rõ đã tìm ở đâu |
| Chưa tìm kỹ / còn nghi ngờ | `needs_review`, không gửi gì |
| Đơn giá và thành tiền trùng tọa độ bất thường | Đưa vào audit, kiểm tra từng cột, không nhân bản box |
| Tự kiểm tra cảnh báo nhưng crop cho thấy đúng | Claude quyết định; ghi `verdict: issue` kèm giải thích nếu vẫn chấp nhận |
| Chưa từng xem crop | Không đánh dấu `verified`, không gửi |
| Quy tắc xoay không làm hóa đơn thẳng | `abort`, dừng toàn bộ hóa đơn |

---

## 5. Bản prompt điều phối cốt lõi

```text
ROLE: High-precision invoice text grounding and bounding-box verification agent (Claude).

INPUT:
- Original PDF from GET /api/documents/{doc_id}/pdf (immutable)
- Pre-filled invoice form from GET /api/documents/{doc_id} (immutable)
- Upright page images produced by the fixed rotation rule
- OCR word-level candidates in upright-image pixel coordinates (scanned PDFs, no text layer)

TASK:
Resolve every target field with exactly one action: "box" (tightest justified text bbox in the
correct page, row, column, and semantic occurrence) or "no_box" (with a valid reason). Produce the tightest justified text bbox. Prioritize correctness over coverage.
Report form/invoice discrepancies without modifying the form.

HARD RULES:
1. Never modify form values or reuse existing backend boxes as ground truth.
2. Decide orientation from the reading direction of the text only.
   landscape -> rotate image 90° counterclockwise; boxes are rotated 180° for the backend.
   portrait  -> no image rotation; boxes are rotated 90° clockwise for the backend.
   If the rule does not make the page upright: abort, log the reason, stop.
3. OCR proposes geometry; Claude selects the correct semantic occurrence.
4. Claude's visual estimates are coarse proposals, never final boxes.
5. For missing candidates: zoom with coordinate grid, OCR the region, then refine.
6. Merge fragmented text only when all fragments belong to the target cell.
7. Inspect a magnified crop of EVERY box; after a fix, recheck only the changed boxes; at most 2 fix rounds, then needs_review.
8. Reject boxes that clip characters, include labels, other cells or rows, or a wrong occurrence.
9. Use no_box only for: null form value; value genuinely absent after zoom + region OCR;
   zero value printed in another form (e.g. KCT). Otherwise uncertain -> needs_review; never invent boxes.
10. Send one PUT per target: "box" only for verified boxes, "no_box" with a recorded reason.
11. Never claim verified without having viewed that box's crop.
12. Report coverage separately from accuracy; append a 4-section report to logs.txt.

OUTPUT:
- One PUT /api/bbox/documents/{doc_id} per resolved target: action "box" (normalized; top-left, top-right, bottom-right, bottom-left) or action "no_box"
- work/<doc_id>/: boxes_upright.json, review sheets, page images with boxes drawn, audit.json
- Appended logs.txt block: bbox completion, form discrepancies, orientation, per-box quality
```

---

## 6. Tiêu chuẩn nghiệm thu / điều kiện được công bố

- **Hoàn thành chạy pipeline:** mỗi id được xử lý đều có một khối trong `logs.txt` — kể cả lỗi API, abort orientation hay phiên bị ngắt (run_batch tự ghi khối lỗi nếu phiên không kịp báo cáo).
- **Hoàn thành gán bbox (coverage):** `HOÀN THÀNH` chỉ khi mọi target đều được gửi thành công bằng `box` hoặc `no_box`; các target còn thiếu được liệt kê kèm lý do.
- **Hoàn thành visual QA:** mỗi box được gửi phải có crop đã xem; box có vấn đề phải có một mục trong box_reviews.
- **Ground truth / benchmark:** cần đánh giá độc lập; không tự tuyên bố bằng điểm khớp chuỗi hay việc backend trả 200.

---

## 7. Chế độ vận hành

### Chế độ 1 — Từng hóa đơn trong Claude Code (VS Code)

Mở thư mục dự án trong VS Code, mở Claude Code và gõ:

```
Xử lý hóa đơn 202606241434
```

`CLAUDE.md` và skill `.claude/skills/xu-ly-hoa-don/SKILL.md` hướng dẫn Claude chạy đủ Bước 0–9. `.claude/settings.json` cấp sẵn quyền cho các lệnh `python -m pipeline.agent` (kể cả Python của `.venv`), chặn đọc `.env` và chặn sửa trực tiếp `logs.txt`.

### Chế độ 2 — Hàng loạt

```
python run_batch.py
```

Không có tham số. Script đọc `ids.txt`, bỏ qua id đã hoàn thành. Mỗi id chạy trong một phiên Claude Code **mới** (context sạch, không mang token của hóa đơn trước). `logs.txt` có dòng mở/đóng batch và tổng kết số hóa đơn COMPLETE / INCOMPLETE / ABORTED / NO_REPORT.

---

## 8. Bài học từ các batch trước (giữ nguyên giá trị)

- Ước lượng thô của mô hình ngôn ngữ-thị giác có thể đọc đúng chữ nhưng lệch mép box nhiều; crop nhìn nhỏ dễ che lỗi → bắt buộc refine theo nét mực và xem crop phóng đại.
- Khớp chuỗi đơn thuần có thể tạo nhiều ứng viên nhưng bỏ sót chữ nhỏ, số trùng, tên thuốc nhiều dòng → luôn để Claude chọn occurrence theo nhãn/hàng/cột.
- Sai orientation 90°/180° hoặc xử lý nhầm `/Rotate` làm sai cả trang → orientation quyết định theo chữ nhìn thấy, quy tắc xoay cố định đã kiểm nghiệm.
- Một số lượt audit trước chưa có bước xem crop cho từng bbox → không tái sử dụng tuyên bố `verified` cũ.
- Hóa đơn là bản scan: OCR là nguồn hình học duy nhất; chữ nhỏ, mờ hoặc bị dấu mộc đè cần `ocr-region` phóng to trước khi kết luận `no_box` (`not_found`).

---

**Lệnh ghi nhớ ngắn:** Fresh API fetch + immutable form → Claude đọc hướng chữ → quy tắc xoay cố định (landscape: ảnh xoay 90° ngược chiều kim đồng hồ, box quay 180°; portrait: ảnh giữ nguyên, box quay 90° cùng chiều kim đồng hồ; không thẳng → abort) → OCR → Claude chọn occurrence + so sai lệch → refine → crop review TỪNG box (tối đa 2 vòng sửa) → convert → PUT `box` cho trường `verified`, `no_box` cho trường null / không có / in dạng khác → audit trung thực vào logs.txt.
