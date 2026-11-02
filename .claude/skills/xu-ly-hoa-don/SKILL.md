---
name: xu-ly-hoa-don
description: Định vị bbox cho một hóa đơn từ invoice-review-api và gửi lên backend. Dùng khi người dùng gõ "Xử lý hóa đơn <id>" (hoặc /xu-ly-hoa-don <id>).
---

# Xử lý hóa đơn `<id>` — quy trình agent

Bạn là agent định vị bbox độ chính xác cao. SOP đầy đủ: `SOP_pipeline_claude_bbox.md`
(chỉ mở khi cần tra cứu; mọi bước bắt buộc đã có dưới đây).
Mọi lệnh chạy từ thư mục gốc dự án: `.venv/Scripts/python -m pipeline.agent <lệnh> ID ...` (bên dưới viết tắt `python`). Đặt `ID` = id người dùng đưa.
Target được gọi bằng **số thứ tự** in trong output và trên sheet (`#3` hoặc `3`) — không cần gõ field_key.

## Luật cứng (không vi phạm)
1. KHÔNG sửa giá trị biểu mẫu, KHÔNG gửi gì khác ngoài PUT action `box` / `no_box`. Sai lệch chỉ ghi vào `audit.json`.
2. KHÔNG dùng bbox cũ trên backend (`field_boxes`, `bbox.fields`) làm đáp án.
3. KHÔNG tự bịa tọa độ cuối. Tọa độ phải đến từ OCR trang, `locate`, hoặc lưới `zoom` + `setbox` — rồi xem crop.
4. KHÔNG đánh `verified` box chưa được xem crop sau lần sửa cuối (lệnh `mark` sẽ chặn).
5. KHÔNG đọc file `.env`, không in session ra màn hình.
6. Mỗi target kết thúc bằng: `verified` (gửi `box`), `no_box` kèm lý do (gửi `no_box`), hoặc `needs_review` (không gửi gì). Không dùng `no_box` cho trường chưa tìm kỹ.
7. **Tiết kiệm token:** chỉ `Read` đúng các ảnh mà lệnh in ra (thumbnail, sheet, ảnh phủ box). KHÔNG `Read` ảnh trang đầy đủ (`*_raw.png`, `*_upright.png`) — dùng `zoom` nếu cần nhìn một vùng. KHÔNG `Read`/Grep các file JSON (`words/*.json`, `candidates.json`, `document.json`) — xem target bằng `show`. Gộp nhiều target vào một lệnh `mark` / `locate`.
8. **Tiết kiệm lượt:** KHÔNG chạy lệnh shell nào ngoài `python -m pipeline.agent` (không `python -c`, grep, cat, ls…).
   Các việc độc lập (Read nhiều sheet, zoom nhiều vùng) gọi song song trong CÙNG một lượt.

## Bước 0–1a — Lấy dữ liệu và render
`python -m pipeline.agent start ID` — chạy ĐÚNG MỘT LẦN mỗi phiên. Lỗi API → lệnh đã tự ghi abort; báo người dùng và DỪNG.

## Bước 1b — Orientation (quyết định của bạn, theo hướng đọc của chữ)
1. `Read` **thumbnail** từng trang `work/ID/pages/page_XXX_raw_thumb.png` (đủ để thấy hướng chữ).
2. Xác định theo HƯỚNG ĐỌC CỦA CHỮ (không theo tỷ lệ khung, không theo cờ /Rotate):
   chữ chạy dọc / hóa đơn nằm nghiêng → **landscape**; chữ đã đọc thẳng trái → phải → **portrait**.
3. `python -m pipeline.agent rotate ID landscape` (hoặc `portrait`). Các trang khác hướng nhau: thêm số trang,
   vd `rotate ID landscape 1 3` rồi `rotate ID portrait 2`.
4. `Read` thumbnail ảnh thẳng `page_XXX_upright_thumb.png` và XÁC NHẬN chữ đã thẳng.
5. **Ngoại lệ** — quy tắc không làm hóa đơn thẳng: KHÔNG thử góc khác, chạy
   `python -m pipeline.agent abort ID "Trang n: <hướng chữ thấy được>, quy tắc xoay không làm thẳng"` rồi DỪNG.

## Bước 2–6a — Máy làm phần còn lại trong một lệnh
`python -m pipeline.agent analyze ID` (manifest → OCR + ứng viên → bản nháp → refine → review vòng 1).
Output in: số target, trường null (đã đặt `no_box`), trường chưa có ứng viên, danh sách `#số field_key` kèm cảnh báo
tự kiểm tra, và đường dẫn sheet + ảnh phủ box cần xem.

## Bước 4 + 6 — Phán đoán của bạn (vòng 1)
`Read` MỌI sheet và ảnh phủ box mà lệnh in ra. Với TỪNG crop kiểm tra:
- Đúng trường / hàng / cột / occurrence? Số trùng (đơn giá vs thành tiền, thành tiền vs tổng cộng) chọn theo nhãn cột và
  hàng. Cảnh báo `duplicate` = hai trường đang dùng cùng một box → ít nhất một box sai chỗ.
- **Gợi ý vị trí thường gặp** (Dùng để chọn đúng chỗ khi cùng giá trị xuất hiện nhiều nơi; xem ảnh phủ box để đối chiếu. Không phải luôn đúng với mọi trường hợp):
  + Số hóa đơn, ký hiệu, ngày hóa đơn: góc trên bên phải.
  + Tên nhà cung cấp, MST bên bán: phía trên, bên trái hoặc giữa (khối người bán, không phải người mua).
  + Thuốc: trong bảng ở giữa hoặc giữa hơi dưới hóa đơn. Mọi trường của CÙNG một thuốc nằm trên cùng 1 row của bảng.
  Box nằm ngoài vùng thường gặp → kiểm tra lại bằng `show` (ứng viên khác) trước khi duyệt.
- **Cùng giá trị ở nhiều nơi → chọn theo nhãn.** Hóa đơn một thuốc thường có thành tiền = cộng tiền hàng. Chỉ có một ứng
  viên hoặc số khớp biểu mẫu KHÔNG chứng minh box đúng chỗ; ô đúng có thể chưa được OCR đọc ra:

  | Trường | Phải nằm ở | Không được nằm ở |
  |---|---|---|
  | `line_amount` | ô Thành tiền của đúng hàng thuốc | Cộng tiền hàng, Tổng cộng |
  | `vat_amount` | cột Tiền thuế của hàng; không có cột này thì dòng Tiền thuế GTGT | hàng khác |
  | `grand_total` | dòng Tổng cộng tiền thanh toán | Cộng tiền hàng, bất kỳ ô nào cùng row với thuốc |
  | `invoice_date` | ngày hóa đơn ở đầu trang | ngày ký số, ngày chuyển đổi cuối trang |

  Cảnh báo `off_row` = box của trường hàng nằm ngoài hàng thuốc: nhìn ảnh phủ box; nếu ô trong hàng có in giá trị thì
  `zoom` + `locate` về ô đó. Chỉ giữ box khi đúng ngoại lệ trong bảng.
- **BẮT BUỘC (không phải gợi ý):** box `grand_total` KHÔNG được cùng row với thuốc, kể cả khi ô đó (vd "Số tiền thanh toán",
  "Thành tiền có thuế") có cùng giá trị với tổng. Phải chuyển về dòng tổng; không tìm được dòng tổng → `mark no_box not_found` kèm ghi chú đã tìm ở đâu.
- `unit_price` và `line_amount`: nếu có 2 chỗ, một chỗ trước chiết khấu và một chỗ sau chiết khấu → LUÔN chọn chỗ sau chiết khấu.
- `grand_total` = tổng thanh toán cuối (đã gồm VAT). `vat_rate_percent` = thuế suất (vd "5%"), không phải tiền thuế.
  Tên thuốc nhiều dòng: bao trọn mọi dòng của ô, không lấn cột khác.
- `invoice_date`: ngày viết dạng chữ ("Ngày 01 tháng 02 năm 2000") → box CẢ CỤM, gồm cả chữ "Ngày";
  ngày viết dạng số sau nhãn ("Ngày: 01/02/2000") → chỉ box phần số "01/02/2000", không lấy nhãn.
- Đủ ký tự, đúng dấu, không cắt đầu/cuối (đặc biệt khi có cảnh báo `near_*` / `cut_*`), không lấn nội dung khác,
  padding không quá rộng/hẹp. Cảnh báo tự kiểm tra chỉ là gợi ý — bạn quyết định.
- Các box phải TÁCH RỜI nhau hoặc box này NẰM TRỌN trong box kia, không giao nhau một phần. Padding cân đối:
  trên ≈ dưới, trái ≈ phải. Chỉ chấp nhận lệch / giao nhau khi bất khả kháng (chữ sát đường kẻ hoặc sát chữ khác)
  và ghi lý do vào `issues` trong audit.
- **So giá trị**: giá trị thật khác biểu mẫu (không tính khác định dạng: 15000 vs 15.000) → ghi `mismatches`; box vẫn
  đặt vào vị trí giá trị THẬT.
- Xem giá trị biểu mẫu, box hiện tại, ứng viên khác: `python -m pipeline.agent show ID <số...>` (hoặc `pending`).
- Box sai / chưa có box: nếu chưa biết vùng thì `zoom ID <trang> x1,y1,x2,y2` (lưới pixel ảnh thẳng), rồi `locate` (dưới).

Ghi quyết định bằng lệnh (gộp nhiều số vào một lệnh):
- Đạt: `python -m pipeline.agent mark ID verified 0 1 2 4 ...` (hoặc `mark ID verified all` khi mọi box đang chờ đều đạt).
- Sửa box (ưu tiên): `python -m pipeline.agent locate ID <số> <trang> x1,y1,x2,y2 [<số> <trang> <vùng> ...]` — vùng rộng
  hơn chữ một chút, gộp nhiều target một lệnh. Lệnh OCR vùng, tìm đúng giá trị biểu mẫu, refine và khóa box.
  Báo KHÔNG THẤY thì in chữ OCR đọc được: nới/dời vùng thử lại một lần, hoặc dùng `setbox` bên dưới.
- Sửa box (dự phòng, khi OCR không đọc được): `python -m pipeline.agent setbox ID <số> <trang> x1,y1,x2,y2 --note "..."`.
- Không có vùng để box: `python -m pipeline.agent mark ID no_box <lý do> <số...> --note "..."`
  - `null_value`: biểu mẫu null (đã đặt sẵn). Hóa đơn lại có in giá trị → giữ `no_box`, ghi `mismatches`.
  - `zero_other_form`: biểu mẫu = 0 nhưng hóa đơn in `KCT`, `KKKNT`, `x`, `-`… (in đúng `0` / `0%` → box bình thường).
    Biểu mẫu ≠ 0 nhưng hóa đơn in `KCT` → cũng `zero_other_form` và ghi `mismatches`.
  - `not_found`: chỉ sau khi đã `zoom` vùng có nhãn/cột tương ứng và `locate` vẫn KHÔNG THẤY; ghi đã tìm ở đâu.
- Chưa chắc: `python -m pipeline.agent mark ID needs_review <số> --note "<lý do>"`.

## Bước 6b — Vòng sửa (tối đa 2 vòng)
Nếu đã `locate` / `setbox`: `python -m pipeline.agent review ID` — chỉ render crop của box vừa sửa. `Read` sheet, rồi `mark` như trên.
Tối đa 3 vòng review (1 vòng đầu + 2 vòng sửa); lệnh tự từ chối vòng thứ 4. Hết vòng mà box vẫn chưa đạt →
`mark ID needs_review <số> --note "<lý do>"` và đi tiếp. Trước khi sang bước 7: không còn trường nào chưa kết luận
mà chưa được tìm bằng mắt.

## Bước 8a — Viết audit.json
Write `work/ID/audit.json`:
```json
{
  "doc_id": "ID",
  "orientation": [{"page": 1, "orientation": "landscape", "upright_ok": true, "note": "chữ chạy dọc từ trên xuống"}],
  "mismatches": [{"field_key": "invoices[0].items[0].quantity", "form_value": 15000, "invoice_value": "1.500", "page": 1, "note": ""}],
  "box_reviews": {
    "invoices[0].invoice.invoice_number": {"verdict": "ok", "issues": [], "note": ""},
    "invoices[0].items[0].expiry_date": {"verdict": "issue", "issues": ["chữ số cuối chạm đường kẻ bảng"], "note": "box vẫn trọn ký tự"}
  },
  "notes": ""
}
```
`box_reviews` phải có MỌI box có bbox (không cần cho trường `no_box`). `verdict`: `ok` | `issue`. Tiếng Việt ngắn gọn.

## Bước 7–9 — Quy đổi, gửi, báo cáo (một lệnh)
1. `python -m pipeline.agent finish ID` = convert → upload → report. Lệnh chặn nếu thiếu `audit.json` hoặc còn box
   `verified` chưa được xem crop sau lần sửa cuối; dừng ở bước nào lỗi thì báo lỗi bước đó.
2. Trả lời người dùng 2–4 dòng: số `box` / `no_box` đã gửi trên tổng target, sai lệch (nếu có), trường còn `needs_review`.