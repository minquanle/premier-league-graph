# Hỏi đáp graph bằng Qwen3-4B trên Kaggle

Ứng dụng dùng Qwen3-4B gốc được phục vụ bằng vLLM trên Kaggle, truy cập từ máy qua Cloudflare tunnel. Neo4j vẫn chạy trên máy. Mô hình fine-tune để trích quan hệ từ báo là nhiệm vụ khác, chưa được dùng thay thế tự động cho Text2Cypher.

## Chạy câu hỏi

Trong PowerShell tại `C:\Player_graph`, khi Docker/Neo4j và notebook phục vụ đang chạy:

```powershell
.\.venv\Scripts\python.exe scripts/chatbot_qa.py "Ai từng dẫn dắt Chelsea?"
.\.venv\Scripts\python.exe scripts/chatbot_qa.py "HLV nào từng dẫn dắt đồng đội của Wayne Rooney?" --mode thinking
.\.venv\Scripts\python.exe scripts/chatbot_qa.py "Trong toàn bộ mạng đồng đội, ai có PageRank cao nhất?" --analytics
```

`--mode auto` đọc `configs/chatbot_policy.json`, được chọn từ tập dev. Có thể chọn thủ công `schema`, `fewshot`, `thinking`. Thực thể được tìm bằng tên chuẩn/bí danh trong graph; một số bí danh CLB thông dụng như MU được bổ sung cục bộ. Tên không rõ hoặc ngoài khả năng của bộ liên kết cần được người dùng làm rõ; chưa đo riêng độ phủ của bộ này. Có thể cung cấp `--params '{"club":"Chelsea"}'` để bỏ qua bước liên kết tên khi gỡ lỗi.

Có thêm `--mode template` để thử model chọn một mẫu từ chính 20 ví dụ và code thực thi Cypher của mẫu đó. Phương án này nhanh nhưng khả năng chọn đúng ý còn yếu; không được chọn làm mặc định. Báo cáo riêng là `reports/chatbot_template_benchmark.json`. Đây là ứng viên bổ sung mang tính thăm dò trên cùng bộ câu hỏi, không phải một tập kiểm chứng độc lập mới.

Khóa và kênh ntfy được đọc từ `.env`; không truyền trong dòng lệnh. Client tự lấy địa chỉ tunnel còn sống từ ntfy và kiểm tra model `qwen3-4b`. Notebook phục vụ có giới hạn thời gian, cần bật lại khi hết phiên.

## Luồng đã triển khai

1. Chuẩn hóa tên từ graph, chọn dạng câu hỏi bằng luật đơn giản.
2. Chọn 4 ví dụ liên quan từ thư viện 20 cặp câu hỏi–Cypher; không đưa các câu benchmark vào thư viện.
3. Model xuất kế hoạch ngắn và Cypher có tham số. Chế độ thinking chỉ bật với một số câu nhiều bước/đếm/giao theo bộ phân loại, không bật cho mọi câu hỏi.
4. Kiểm tra tập con Cypher chỉ đọc, tham số, nhãn, miền hai đầu quan hệ, DISTINCT, LIMIT; chạy EXPLAIN rồi truy vấn với thời hạn. Tối đa hai lượt sửa bằng lỗi thật, không có đáp án chuẩn. Kết quả rỗng không tự kích hoạt sửa.
5. Lấy bằng chứng theo từng người/CLB kết quả. Đường suy diễn đồng đội–HLV được lưu riêng, không gán thành bằng chứng báo chí trực tiếp.
6. Model chọn cấu trúc trả lời gồm mã dòng và mã nguồn. Chương trình kiểm tra đủ dòng, thứ tự và nguồn thuộc đúng dòng, rồi hiển thị tên/số liệu/URL từ dữ liệu gốc. Nếu cấu trúc model sai thì dùng bộ dựng câu trả lời xác định và ghi rõ `fallback_used`.

Đây là trả lời có cấu trúc, không phải kiểm chứng ngữ nghĩa của văn bản tự do bằng một LLM khác. Việc khóa nguồn theo dòng ngăn gắn nhầm URL; không chứng minh cạnh gốc hay câu bằng chứng thật sự đúng về bóng đá. Các chỉ số và kết quả lịch sử được trình bày là thông tin trong graph, không tự khẳng định tình trạng hiện tại.

Các kiểm tra Cypher là tập con phục vụ thử nghiệm cục bộ, không phải parser/sandbox Cypher hoàn chỉnh. Kết nối dùng phiên READ_ACCESS và giới hạn thời gian; không giả định đã tạo được tài khoản read-only riêng trên Neo4j Community.

## Phân tích bằng công cụ

`analytics.py` cung cấp ba hàm: đếm người từng thi đấu cho CLB, xếp hạng CLB theo số người, và PageRank trên toàn bộ mạng TEAMMATE_OF vô hướng (damping 0,85). Model chọn hàm/tham số, Neo4j hoặc NetworkX tính kết quả. Không thực thi Python tùy ý do model sinh. Bản này nên được mô tả là tool use; chưa phải thực nghiệm PoT sinh chương trình tổng quát. Ba bài thử công cụ là smoke test, không phải benchmark lớn.

## Thiết kế thực nghiệm

`configs/chatbot_examples.json`: 20 ví dụ. `configs/chatbot_benchmark.json`: 8 câu dev và 24 câu test, gồm tra cứu, nhiều bước, đếm, giao và rỗng. Các truy vấn chuẩn đã EXPLAIN và thực thi trên graph. Có giao nhau về họ truy vấn và một cặp ví dụ/test tương đương truy vấn nhưng khác cách hỏi; tập test kiểm tra diễn đạt tách riêng, không chứng minh tổng quát hóa sang mọi họ truy vấn chưa gặp.

Ba cấu hình dùng chung schema, validator, graph và số lần sửa: chỉ schema; few-shot; few-shot có thinking chọn lọc. Khi thinking bật, dùng temperature 0,6 theo cách dùng của Qwen, trong khi không thinking dùng 0; vì vậy đây là so sánh cấu hình sử dụng thực tế, không phải thí nghiệm chỉ đổi duy nhất một cờ. Hai request đồng thời dùng cùng GPU; thời gian là độ trễ dưới tải này, chưa bao gồm khởi động notebook. Trình tự chạy cố định và prefix cache có thể ảnh hưởng thời gian.

Benchmark cấp sẵn tham số tên chuẩn để cô lập Text2Cypher. Không gửi truy vấn chuẩn, nhãn category, đáp án hay lỗi so với đáp án vào model. Truy vấn chuẩn chỉ được dùng sau khi sinh để chấm khớp toàn bộ dòng kết quả trên graph cố định. Không chấm bằng chuỗi Cypher giống nhau. Điểm sau sửa được tách khỏi điểm đúng ngay lần đầu.

Các câu trả lời chỉ được thử trên một số câu đại diện mỗi cấu hình. Độ đúng của cấu trúc chọn dòng/nguồn được ghi trước khi fallback, không gộp fallback thành năng lực trả lời của model. Chất lượng nguồn và sự thật ngoài đời chưa được chấm độc lập.

## Kết quả vòng thử ngày 20/11/2026

Trên 24 câu test (được cung cấp tên chuẩn), số câu khớp truy vấn chuẩn sau tối đa hai lượt sửa là: chỉ schema 6/24; few-shot 12/24; few-shot kèm thinking chọn lọc 14/24; chọn mẫu có sẵn 10/24. Thời gian trung bình phần sinh/kiểm tra/chạy truy vấn tương ứng là 6,56; 3,69; 25,39; 0,64 giây. Đây không phải tổng thời gian hỏi đáp, chưa gồm sinh câu trả lời hay khởi động Kaggle. Các khoảng tin cậy rộng và chồng lấn; chưa đủ kết luận một cách vượt trội cho mọi câu hỏi.

Chính sách đã chọn từ dev vẫn là few-shot cho Text2Cypher để cân bằng độ đúng và thời gian; thinking là tùy chọn thử thêm. Tập test cho thấy cần cải thiện trước khi dùng tin cậy: cấu hình tốt nhất về điểm truy vấn trong lần thử này vẫn chỉ đạt 58,3%. Với thống kê được hỗ trợ, ưu tiên công cụ xác định thay cho để mô hình tự viết phép tính. Ba smoke test công cụ đều chọn đúng sau khi thêm ví dụ, nhưng chưa đo độ phủ trên bộ câu hỏi phân tích lớn.

Bản dựng trả lời gắn nguồn theo dòng ngăn nguồn không thuộc kết quả và bảo toàn tên/số liệu; không sửa được một truy vấn đã hiểu sai câu hỏi hoặc một cạnh sai từ graph. Chưa có bằng chứng đạt 90% chất lượng hỏi đáp. Muốn đánh giá lần tối ưu tiếp theo cần bổ sung tập dev, cải thiện nhận diện ý định/kế hoạch hoặc fine-tune riêng Text2Cypher, rồi chấm trên tập test mới.

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_chatbot.py --split dev
.\.venv\Scripts\python.exe scripts/benchmark_chatbot.py --split test
.\.venv\Scripts\python.exe scripts/benchmark_chatbot_tools.py
.\.venv\Scripts\python.exe -m pytest -q
```

Kết quả nằm trong `reports/chatbot_benchmark_dev.json`, `reports/chatbot_benchmark_test.json`, `reports/chatbot_tools_smoke.json`. Mỗi báo cáo lưu mã băm prompt/mã kiểm tra và dữ liệu. Chạy lại sẽ tiếp tục phần chưa có, không tự ghi đè thực nghiệm khác phiên bản. `dev_v0` là vòng phát triển trước khi bổ sung kiểm tra DISTINCT/miền quan hệ; `test_interrupted` là lần bị dừng để xử lý lỗi phát hiện bởi unit test, không được gộp vào kết quả test cuối. Chính sách được chọn từ dev trước khi đọc kết quả test cuối.
