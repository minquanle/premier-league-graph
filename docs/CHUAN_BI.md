# Chuẩn bị triển khai Player Graph

Cập nhật: 29/10/2026, giờ Việt Nam. Đây là kế hoạch làm việc, chưa phải kết quả thực nghiệm.

**Ghi chú sau triển khai cùng ngày:** pilot 300 bài song ngữ đã chạy không dùng API. Kế hoạch bên dưới được giữ như tài liệu chuẩn bị; trạng thái thực tế, số đếm và giới hạn nằm tại `reports/PILOT_REPORT.md`, `reports/pilot_summary.json` và `configs/project_plan.json`. Các mục ghi “chưa hoàn thành” bên dưới phản ánh thời điểm chuẩn bị, không thay thế các file kết quả mới.

## 1. Yêu cầu và những điểm đã chốt

- File `Đề bài tập cuối kỳ.txt` là toàn bộ yêu cầu chính thức, theo xác nhận của người dùng.
- Người dùng xác nhận lỗi gõ trong đề: yêu cầu chung là **2.000 nút**, không phải hai triệu.
- Đề cho phép thu hẹp sang Ngoại hạng Anh với tối thiểu 1.000 nút. Kế hoạch vẫn nhắm **2.000 thực thể hợp lệ sau hợp nhất tên, 5.000 quan hệ sau khử trùng và ít nhất 10 loại quan hệ có dữ liệu thực**. Không tự giảm mục tiêu xuống 1.000 nếu thu thập khó.
- Dùng **cả báo chí tiếng Việt và tiếng Anh ngay từ đầu**.
- Người dùng có thể dùng công cụ không mất phí hoặc xử lý qua 9Router, nhưng **chưa chọn endpoint, model hay ngân sách**. Chuẩn bị cả hai chế độ; cấu hình API để trống và mặc định tắt.
- Dữ liệu graph phải vào một hệ quản trị phù hợp; ưu tiên đề xuất Neo4j Community.
- Báo cáo tối thiểu 10 trang và có liên kết GitHub. Hạn nộp trước 23:59 ngày 11/11/2026, Asia/Saigon.
- Đề yêu cầu chất lượng trên 90%, đồng thời ghi sẽ đánh giá chất lượng ở bước cuối kỳ sau. Cần chuẩn bị bằng chứng đánh giá ngay từ pipeline; chưa có định nghĩa chấm chi tiết của giảng viên.
- Các thuật toán và cách demo bên dưới là đề xuất phục vụ bài làm, không phải yêu cầu bổ sung của giảng viên.

## 2. Phạm vi nghiên cứu đề xuất

Tên làm việc: **Xây dựng và phân tích mạng lưới quan hệ cầu thủ, huấn luyện viên và câu lạc bộ xoay quanh Ngoại hạng Anh từ dữ liệu báo chí song ngữ**.

- Khoảng thời gian thu thập ban đầu: bài xuất bản từ 01/01/2010 đến 29/10/2026. Khi mở rộng ngày kết thúc phải ghi lại phiên bản corpus.
- Bao gồm các CLB từng dự Ngoại hạng Anh trong giai đoạn nghiên cứu, không chỉ các đội đang dự giải hoặc nhóm CLB lớn.
- Giữ CLB ngoài giải khi tham gia trực tiếp giao dịch/sự kiện với thực thể thuộc phạm vi. Không mở rộng đệ quy sang toàn bộ bóng đá thế giới.
- Người đại diện chỉ được đưa vào khi có quan hệ đại diện hoặc sự kiện cụ thể được bài báo chứng minh.
- Ghi nhãn `scope_role=core|context`: core là thực thể có liên hệ Ngoại hạng Anh có bằng chứng; context là đối tác trực tiếp ngoài giải. Báo cáo riêng hai nhóm, không để nút ngoài giải che khuất thiếu hụt phạm vi chính.
- Mỗi nút tính vào chỉ tiêu phải có tên chuẩn, loại thực thể, bằng chứng thuộc phạm vi và ít nhất một cạnh hợp lệ. Thực thể chỉ được nhắc qua nhưng chưa có quan hệ được giữ ở dữ liệu trung gian.
- Không dùng số lượng bài báo, mùa giải, câu văn, bí danh hoặc bản ghi sự kiện làm số nút bóng đá để đạt chỉ tiêu.

## 3. Nguồn dữ liệu và kiểm tra ban đầu

Đã kiểm tra qua HTTP ngày 29/10/2026; đây là mẫu nhỏ, chưa kiểm chứng toàn bộ kho lưu trữ.

- **Bóng đá Plus:** trang chủ, chuyên mục Ngoại hạng Anh và bài mẫu trả HTTP 200. Bài mẫu có nội dung tại `#postContent`; bộ selector chung thử trước đó không lấy được nội dung. Cần adapter riêng và kiểm tra bài Premium. Chuyên mục chuyển nhượng Ngoại hạng Anh được phát hiện từ trang chủ, chưa kiểm tra độ phủ lịch sử.
- **VnExpress:** trang Thể thao, chuyên mục và bài mẫu trả HTTP 200. Selector thử thu được 27 đoạn, tổng 7.425 ký tự ứng viên. Con số này chưa loại hoàn toàn nội dung lặp hoặc phần phụ.
- **24h:** chuyên mục và bài mẫu trả HTTP 200. Phát hiện 28 URL bài dạng `/bong-da/...-c48a<number>.html` trong trang chuyên mục. Mẫu thử có 33 đoạn ứng viên, tổng 10.141 ký tự. Không được giả định mã chuyên mục Ngoại hạng Anh `c149` cũng là mã trong URL bài; phải loại trang tỷ số/trực tiếp dạng `mid...`.
- Đã đọc robots.txt của ba nguồn Việt Nam. Khi triển khai cần lưu snapshot quy tắc, kiểm tra lại trước phiên thu thập, giới hạn tốc độ và không thu thập đường dẫn bị chặn. HTTP 200 hoặc robots cho phép không tự chứng minh quyền tái phân phối toàn văn.
- **Nguồn tiếng Anh chưa chốt:** BBC và The Guardian có tuyên bố hạn chế việc xây dataset/khai thác AI trong robots.txt được đọc ở phiên chuẩn bị; không mặc định dùng làm corpus. Sky Sports và Football365 bị timeout ở bước kiểm tra từ môi trường hiện tại. ESPN chặn tác nhân ChatGPT-User; chưa chọn làm nguồn. Tiếp tục tìm nguồn tiếng Anh phù hợp hoặc corpus có điều kiện sử dụng rõ ràng. Đây là đầu việc ưu tiên vì song ngữ đã được chốt.
- Trang Premier League/CLB chính thức có thể hỗ trợ đối chiếu tên hoặc sự kiện; không dùng thay thế nguồn báo chí cho các cạnh nộp bài mà không ghi rõ nguồn.

Chiến lược thu thập: chuyên mục, sitemap, RSS và liên kết bài công khai; lưu trạng thái HTTP, URL chuẩn, ngày đăng, ngày cập nhật, thời điểm tải và hash. Retry có giới hạn và backoff; gặp chặn truy cập thì ghi lỗi và đổi nguồn phù hợp, không lặp vô hạn.

Chưa ước lượng cố định cần bao nhiêu bài để đạt 2.000 nút. Pilot phải đo số thực thể mới và cạnh mới sau khử trùng theo từng lô, ngôn ngữ và năm; lấy đó làm cơ sở ước lượng khối lượng.

## 4. Mô hình dữ liệu

Thực thể chính: `Person` và `Club`. `Person` có vai trò theo ngữ cảnh/thời gian như cầu thủ, HLV, người đại diện; một người từng làm cầu thủ rồi làm HLV vẫn là một thực thể. Tổ chức đại diện có thể được bổ sung nếu corpus có bằng chứng; không đưa vào chỉ để tăng số nút.

- Entity: `entity_id`, `canonical_name`, `aliases`, `entity_type`, `roles`, `scope_role`, `scope_evidence_ids`.
- Article: `article_id`, `canonical_url`, `source`, `language`, `title`, `published_at`, `modified_at`, `retrieved_at`, `content_hash`, `duplicate_cluster_id`, `clean_text`.
- Assertion: `assertion_id`, `subject_id`, `relation_type`, `object_id`, `event_id`, `event_date`, `date_precision`, `assertion_status`, `article_id`, `evidence_text`, `evidence_start`, `evidence_end`, `extractor_version`, `model_id`, `review_status`.
- Giữ ngày đăng và ngày sự kiện riêng. Không suy ra ngày chuyển nhượng từ ngày đăng nếu bài không nói rõ; để null/độ chính xác thấp và lưu câu gốc.
- Mỗi cạnh có thể có nhiều bằng chứng; nhiều bài về cùng sự kiện không tạo nhiều cạnh nộp bài.

Danh sách 12 loại quan hệ ứng viên và ràng buộc đầu cạnh nằm trong `configs/project_plan.json`. Ít nhất 10 loại phải thực sự xuất hiện và đạt tiêu chí chất lượng; khai báo nhãn trong schema không được tính là đã đạt.

Một số loại biểu diễn trạng thái (thi đấu cho, dẫn dắt), số khác biểu diễn sự kiện (chuyển nhượng, bổ nhiệm). Không tự phát sinh cả trạng thái lẫn sự kiện chỉ từ một từ khóa để tăng số cạnh; chỉ xuất loại phù hợp với chứng cứ và ghi rõ quy tắc nếu có suy luận. Không tạo nhãn đảo chiều như “mua cầu thủ”/“được CLB mua” để đủ số loại.

`assertion_status`: `completed`, `ongoing`, `reported`, `rumor`, `negated`, `hypothetical`, `uncertain`. Đây là trạng thái theo nội dung bài, không phải chứng nhận sự thật khách quan. Tin đồn chuyển nhượng không thành cạnh chuyển nhượng hoàn tất. Có thể lưu lớp tin đồn riêng; bộ thống kê nghiệm thu bảo thủ loại rumor/negated/hypothetical/uncertain. Quan hệ đàm phán chỉ được giữ khi văn bản thực sự khẳng định có đàm phán.

Khóa khử trùng sự kiện phải xét chủ thể, đối tượng, loại quan hệ và thời điểm; không dùng riêng cặp tên, cũng không dùng ngày đăng làm khóa sự kiện. Với sự kiện thiếu thời gian, đưa các ứng viên trùng vào hàng chờ rà soát thay vì coi mỗi bài là một sự kiện mới. Báo cáo cả số cặp quan hệ duy nhất và số sự kiện để minh bạch cách đạt 5.000 cạnh.

## 5. Pipeline NLP và giải quyết tên

1. Tải và lưu bản thô; khử trùng URL và bài đăng lại.
2. Trích nội dung, metadata; loại menu, quảng cáo, bình luận, link bài liên quan. Kiểm tra thủ công theo nguồn và theo năm.
3. Nhận diện ngôn ngữ, chuẩn hóa Unicode, tách câu và giữ offset. Nội dung gốc là mốc đối chiếu, không thay bằng bản dịch mất dấu vết.
4. Phát hiện thực thể và ứng viên bí danh. NER đơn thuần chưa giải quyết được ai quan hệ gì với ai.
5. Hợp nhất tên song ngữ dựa trên bí danh, ngữ cảnh CLB, vai trò và thời gian. `MU`, `Man Utd`, `Manchester United` có thể cùng thực thể; `Manchester` không tự gộp. Các người trùng họ/tên phải có ngữ cảnh phân biệt. Các câu mơ hồ giữ ở hàng chờ.
6. Trích quan hệ ở mức câu/đoạn có ngữ cảnh: xác định chủ thể, đối tượng, quan hệ, thời gian, phủ định, tin đồn và câu bằng chứng. Không nối tất cả người cùng xuất hiện trong bài.
7. Kiểm tra schema, loại đầu cạnh và bằng chứng là chuỗi con thật của văn bản đã lưu. Kết quả LLM không có bằng chứng hợp lệ bị loại/đưa chờ duyệt.
8. Khử trùng sự kiện, gộp bằng chứng, rà soát xung đột; chỉ xuất lớp đạt điều kiện vào graph nghiệm thu.
9. Import Neo4j bằng ID ổn định; chạy lại phải cho kết quả nhất quán, không nhân đôi nút/cạnh.

Hai phương án trích xuất cần so sánh trên cùng mẫu: baseline bằng từ điển + luật ngôn ngữ; phương án có LLM qua 9Router kết hợp validator. Chưa cam kết baseline tự đạt 90% hoặc mô hình nào tốt nhất trước đánh giá. Chỉ regex tên người hoặc danh sách cầu thủ có sẵn không đủ làm toàn bộ phần NLP.

9Router hỗ trợ endpoint tương thích OpenAI theo tài liệu dự án. Dùng cấu hình riêng `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`; không sửa cấu hình hệ thống của người dùng. Endpoint ví dụ trong tài liệu không được coi là endpoint đang chạy trên máy này.

Cache theo hash văn bản + phiên bản prompt/schema + model thực tế. Ghi số request, token đầu vào/đầu ra và các lần retry; không gọi lại khi cache hợp lệ. Nếu router đổi model thì ghi model thực tế khi biết; không coi kết quả các model khác nhau là một cấu hình thử nghiệm. Giới hạn request và concurrency; chỉ thực thi hạn mức tiền khi có thông tin giá đáng tin cậy. Không mặc định 9Router đồng nghĩa miễn phí.

## 6. Đánh giá trên 90% sao cho có bằng chứng

Định nghĩa nội bộ đề xuất: **precision quan hệ**, với một cạnh chỉ đúng khi đúng hai thực thể, đúng chiều/loại quan hệ, đúng trạng thái và được câu bằng chứng hỗ trợ; thời gian phải đúng khi có ghi. Cần nói rõ đây là định nghĩa của nhóm vì đề chưa định nghĩa metric.

- Precision = số cạnh đúng / số cạnh được đánh giá. Báo cáo riêng kết quả tự động trước duyệt và mạng cuối sau duyệt.
- Tạo tập gán nhãn thủ công từ cả hai ngôn ngữ, nhiều nguồn, năm và loại quan hệ; có cả câu không có quan hệ, phủ định, tin đồn, trùng tên và nhắc lại quá khứ.
- Chia development/holdout theo cụm sự kiện và bài gần trùng, gồm cả bản dịch/đăng lại khác ngôn ngữ. Không chỉnh luật/prompt theo tập holdout rồi vẫn gọi đó là kết quả độc lập.
- Trên tập bài đã gán nhãn đầy đủ, đo thêm recall và F1 để phát hiện phương án bỏ quá nhiều cạnh khó. Phân biệt đánh giá trích xuất từ văn bản với kiểm chứng sự kiện ngoài đời.
- Kiểm toán mạng cuối: đề xuất lấy ngẫu nhiên đơn giản 400 cạnh duy nhất bằng seed cố định để ước lượng precision chung. Kiểm tra thêm tối thiểu 20 cạnh mỗi loại nếu đủ dữ liệu; mẫu bổ sung này là chẩn đoán từng loại, không cộng thẳng để tính tỷ lệ chung vì lệch phân bố.
- Báo cáo cỡ mẫu, số đúng/sai, phân bố theo ngôn ngữ/loại và khoảng tin cậy 95% Wilson cho mẫu ngẫu nhiên. Điểm mẫu >90% không chứng minh tuyệt đối toàn bộ graph >90%; nếu muốn kết luận mạnh hơn, xem cận dưới khoảng tin cậy.
- Đề xuất mục tiêu nội bộ precision mẫu >=95% để có biên sửa lỗi; đây không phải ngưỡng bổ sung của đề. Với loại hiếm có dưới 20 cạnh, đánh giá hết và nêu hạn chế.
- Nếu có hai người gán nhãn, đo mức đồng thuận và xử lý bất đồng. Nếu làm một mình, ghi đúng thực tế và nhờ thành viên kiểm tra chéo khi có thể; không bịa phân công hoặc số người đánh giá.

Tiêu chí trước khi nộp: đủ quy mô sau lọc và hợp nhất; >=10 loại thật; không cạnh thiếu bằng chứng; không endpoint mồ côi; thống kê đếm theo truy vấn tái lập; lỗi còn lại và mức đánh giá phải được trình bày trung thực.

## 7. Công nghệ và trạng thái máy

Đề xuất: Python 3.11 trong môi trường `.venv` riêng; HTTP client + BeautifulSoup/Trafilatura cho nội dung; JSONL/SQLite cho tiến trình; Neo4j Community làm kho graph chính. Chọn và khóa phiên bản sau kiểm tra tương thích, không dùng môi trường toàn cục hiện tại làm môi trường dự án.

- Đã kiểm tra Python mặc định 3.14.6; `py -0p` có đăng ký Python 3.11. Chưa tạo môi trường riêng hay xác nhận đủ dependency cho 3.11.
- Git 2.52.0, Node 24.11.1, Docker CLI 29.1.2 có sẵn. Thư mục hiện chưa là Git repository.
- Docker Engine chưa kết nối được: thiếu named pipe `dockerDesktopLinuxEngine` tại lúc kiểm tra. Cần kiểm tra/khởi động Docker Desktop và khả năng chạy Linux container khi bắt đầu cài Neo4j; không kết luận Docker hỏng.
- `neo4j` Python driver, `trafilatura`, `spacy`, `pytest` chưa thấy trong Python mặc định; `torch`/`transformers` mới kiểm tra có module, chưa xác nhận import, GPU hay khả năng chạy mô hình.
- Máy báo khoảng 15,3 GiB RAM vật lý qua WMI. Chưa benchmark mô hình, không chọn mô hình lớn chỉ dựa vào thư viện có sẵn.
- Neo4j dùng volume lưu dữ liệu và phiên bản image cố định. Nếu dùng GDS, phải chọn plugin tương thích và kiểm tra thủ tục chạy thật trước khi đưa kết quả vào báo cáo. NetworkX có thể dùng đối chiếu/tính toán bổ sung; không thay thế yêu cầu lưu graph vào DB.

Demo tối thiểu đề xuất: mở graph trong Neo4j Browser, tìm cầu thủ/CLB, lọc quan hệ/thời gian và truy ngược bài báo. Chưa cần xây web app riêng trước khi hoàn thành dữ liệu.

## 8. Pilot và điều kiện mở rộng

Pilot khoảng 300 bài hợp lệ, dự kiến 150 Việt + 150 Anh; phân bố nhiều năm/CLB và chủ đề chuyển nhượng, hợp đồng, HLV, đại diện, phát biểu/xung đột. Chỉ khởi động phần Anh khi tìm được nguồn phù hợp. Chủ đích lấy đủ chủ đề cho development; kết quả precision chung cuối vẫn cần mẫu ngẫu nhiên riêng.

Kết quả bắt buộc của pilot:

- Số URL phát hiện/tải thành công/bài sạch/bài trùng theo nguồn và ngôn ngữ.
- Số thực thể duy nhất, core/context, số thực thể mới theo lô; số bí danh đã gộp và ca mơ hồ.
- Số cạnh ứng viên, bị loại và được chấp nhận; số loại quan hệ thực sự tìm được; danh sách loại hiếm/chưa gặp.
- Đánh giá thủ công ban đầu, nhóm lỗi lớn và bản sửa tiếp theo; không dùng điểm pilot làm kết quả nghiệm thu toàn mạng.
- Nếu dùng API: latency, token, request lỗi/retry và chi phí có căn cứ. Nếu chưa biết giá thì ghi chưa biết, không báo 0 USD.
- Ước lượng quy mô và thời gian từ tỷ lệ thực thể/cạnh mới; không ngoại suy tuyến tính mù quáng khi tốc độ tìm thực thể mới đang giảm.

Chỉ mở rộng khi đã xác minh lưu được graph, đếm đúng, lấy bằng chứng được và có hướng khả thi cho >=10 loại. Nếu thiếu nút: mở rộng năm, CLB, nguồn báo trong phạm vi trước; nếu thiếu loại: tìm bổ sung bài đúng chủ đề, không tách nhãn tùy tiện. Không coi bài viết là nút bóng đá hoặc sinh cạnh đồng xuất hiện để bù số.

## 9. Lịch dự kiến đến hạn nộp

- 29–30/10: khóa định nghĩa dữ liệu, tìm nguồn Anh, chuẩn bị môi trường và mẫu gán nhãn.
- 31/10–01/11: pilot song ngữ, import Neo4j nhỏ và sửa lỗi NLP/đếm trùng.
- 02–05/11: mở rộng thu thập, theo dõi tốc độ tăng thực thể/cạnh và tỷ lệ lỗi.
- 06–07/11: hợp nhất thực thể, rà soát quan hệ hiếm, kiểm toán chất lượng và xử lý lỗi.
- 08–09/11: thống kê, truy vấn/thuật toán minh họa, viết báo cáo và lưu ảnh thật.
- 10/11: chạy lại quy trình, kiểm tra gói nộp, GitHub và bản sao lưu.
- 11/11: dành thời gian dự phòng, nộp trước 23:59 giờ Việt Nam.

Các mốc là kế hoạch, không có automation được tạo và không phải cam kết đã hoàn thành.

## 10. Báo cáo và gói nộp

Khung báo cáo dự kiến 12–15 trang để có dư địa cho yêu cầu tối thiểu 10 trang: giới thiệu/phạm vi; phân công thực tế; nguồn dữ liệu; schema và quy tắc đếm; công nghệ; tiền xử lý; NER và hợp nhất tên; trích quan hệ; đánh giá; thống kê mạng; minh họa; hạn chế/kết luận và GitHub.

Thống kê đề xuất: số thực thể/cạnh theo loại, nguồn, ngôn ngữ, thời gian; độ bậc, thành phần liên thông, tỷ lệ nút cô lập. PageRank và phân cụm cộng đồng là phần bổ sung khi phù hợp. Khi chiếu graph có hướng/nhiều quan hệ sang graph thuật toán phải nêu loại cạnh, hướng, trọng số, cửa sổ thời gian; không kết luận “ảnh hưởng” chỉ từ số lần lên báo.

Gói nộp dự kiến có mã nguồn, file khóa dependency, hướng dẫn tái lập, cấu hình mẫu, schema, dữ liệu graph xuất phù hợp quyền sử dụng, truy vấn đếm/kiểm tra, tập đánh giá và kết quả thật, báo cáo PDF/nguồn, liên kết GitHub. Không đưa API key, mật khẩu hoặc toàn văn báo chưa được phép tái phân phối lên repository công khai.

Thông tin người dùng cần bổ sung khi viết báo cáo/đẩy GitHub: tên môn/lớp, thành viên và MSSV, phân công thực tế, tài khoản/repository đích. Chưa cần các thông tin đó để làm pilot; không tự bịa.

## 11. Những việc chưa hoàn thành

1. Chốt ít nhất một nguồn báo chí tiếng Anh phù hợp và kiểm tra bài mẫu/lịch sử.
2. Tạo môi trường dự án; chạy được Neo4j và thử import thật.
3. Xây và đánh giá pipeline; chưa có bất kỳ kết quả 2.000 nút, 5.000 cạnh hay >90% nào.
4. Chọn cấu hình 9Router nếu sử dụng. Hiện không gọi API và không phát sinh phí mô hình trong bước chuẩn bị.
5. Xác minh đủ 10 loại quan hệ từ pilot; danh sách schema hiện chỉ là ứng viên.

## 12. Tài liệu tham chiếu

- Đề gốc trong thư mục, cùng các xác nhận của người dùng trong chat.
- [Neo4j trong Docker](https://neo4j.com/docs/operations-manual/current/docker/introduction/).
- [Plugin Neo4j trong Docker](https://neo4j.com/docs/operations-manual/current/docker/plugins/).
- [Trafilatura: cách trích nội dung và metadata](https://trafilatura.readthedocs.io/en/stable/extraction-overview.html).
- [Tài liệu tích hợp của dự án 9Router](https://github.com/decolua/9router/blob/master/gitbook/content/en/integration/other-tools.md).
- Các URL nguồn và bằng chứng kiểm tra cục bộ được lưu trong `reports/preflight.json`.
