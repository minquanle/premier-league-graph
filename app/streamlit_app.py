"""Premier League Graph: network overview, node portrait and multi-hop question answering.

Run:  .venv/Scripts/python.exe -m streamlit run app/streamlit_app.py
Needs Neo4j (docker compose up -d) and, for questions, the Qwen3-4B endpoint served from Kaggle.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from player_graph.chatbot import agent, portrait  # noqa: E402
from player_graph.chatbot.backend import KaggleClient  # noqa: E402
from player_graph.chatbot.pipeline import ask  # noqa: E402
from player_graph.chatbot.query import Graph  # noqa: E402

RUN = 'student_v4'
KIND_COLOR = {'Club': '#2563eb', 'Player': '#16a34a', 'Coach': '#ea580c', 'Agent': '#9333ea', 'Person': '#64748b'}
REL_COLOR = {'PLAYS_FOR': '#93c5fd', 'MANAGES': '#fdba74', 'TEAMMATE_OF': '#cbd5e1', 'PLAYED_UNDER': '#fcd34d',
             'TRANSFERRED_TO': '#5eead4', 'LOANED_TO': '#a5f3fc', 'CRITICIZES': '#fca5a5', 'CONFLICTS_WITH': '#f87171',
             'APPOINTED_MANAGER_OF': '#86efac', 'DISMISSED_MANAGER': '#f9a8d4', 'REPRESENTS': '#d8b4fe',
             'EXTENDED_CONTRACT_WITH': '#bef264', 'NEGOTIATES_TO_SIGN': '#fde68a', 'RESIGNED_FROM': '#e5e7eb',
             'TRANSFER_FLOW': '#99f6e4'}
REL_VI = {'PLAYS_FOR': 'thi đấu cho', 'MANAGES': 'dẫn dắt', 'TEAMMATE_OF': 'đồng đội', 'PLAYED_UNDER': 'chơi dưới quyền',
          'TRANSFERRED_TO': 'chuyển đến', 'LOANED_TO': 'cho mượn tới', 'CRITICIZES': 'chỉ trích',
          'CONFLICTS_WITH': 'mâu thuẫn', 'APPOINTED_MANAGER_OF': 'được bổ nhiệm HLV',
          'DISMISSED_MANAGER': 'sa thải HLV', 'REPRESENTS': 'đại diện cho', 'EXTENDED_CONTRACT_WITH': 'gia hạn với',
          'NEGOTIATES_TO_SIGN': 'đàm phán chiêu mộ', 'RESIGNED_FROM': 'từ chức', 'TRANSFER_FLOW': 'dòng chuyển nhượng'}
CHECK_VI = {'V0': 'kế hoạch sai kiểu (hop không khớp loại thực thể)', 'V1': 'bỏ quên một thực thể trong câu hỏi',
            'V2': 'vai trò không khớp, có thể ngược chiều quan hệ', 'V3': 'câu hỏi nhắc tới một quan hệ mà kế hoạch không có',
            'V4': 'kế hoạch không trả về kết quả nào', 'V5': 'loại đáp án không khớp với câu hỏi'}
KIND_VI = {'Club': 'CLB', 'Player': 'Cầu thủ', 'Coach': 'HLV', 'Agent': 'Người đại diện', 'Person': 'Nhân vật'}

st.set_page_config(page_title='Premier League Graph', page_icon='⚽', layout='wide')
st.markdown('''<style>
.block-container {padding-top: 1.6rem; max-width: 1350px;}
[data-testid="stMetricValue"] {font-size: 1.6rem;}
.legend span {display:inline-block; margin-right:14px; font-size:0.85rem;}
.legend i {display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:5px; vertical-align:-1px;}
.step {border-left: 3px solid #2563eb; padding: 2px 10px; margin: 4px 0;}
.quote {color:#475569; font-size:0.9rem; border-left:3px solid #e2e8f0; padding-left:10px; margin:4px 0 10px 0;}
</style>''', unsafe_allow_html=True)


@st.cache_resource
def graph():
    return Graph()


def client():
    if st.session_state.get('client') is None:
        try:
            st.session_state.client = KaggleClient.discover(wait_minutes=0.1)
        except Exception:
            st.session_state.client = None
    return st.session_state.client


@st.cache_data
def report(name):
    path = ROOT / 'reports' / name
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


@st.cache_data(ttl=3600)
def counts():
    g = graph()
    nodes = g.run('''MATCH (n:FootballEntity) RETURN [l IN labels(n) WHERE l IN ['Club','Player','Coach','Agent']] AS labels''')
    edges = g.run('''MATCH (:FootballEntity)-[r]->(:FootballEntity) RETURN type(r) AS relation, count(*) AS n ORDER BY n DESC''')
    names = g.run('MATCH (n:FootballEntity) RETURN n.canonical_name AS name ORDER BY name')
    kinds = {k: sum(k in r['labels'] for r in nodes) for k in ('Club', 'Player', 'Coach', 'Agent')}
    return len(nodes), kinds, edges, [r['name'] for r in names]


def legend():
    st.markdown('<div class="legend">' + ''.join(f'<span><i style="background:{c}"></i>{KIND_VI[k]}</span>'
                                               for k, c in KIND_COLOR.items() if k != 'Person') + '</div>',
                unsafe_allow_html=True)


def draw(edges, focus=(), height=560):
    """Interactive graph of (source, relation, target) edges; focus nodes are drawn larger."""
    if not edges:
        st.info('Không có cạnh nào để vẽ.')
        return
    names = {n for a, _, b in edges for n in (a, b)}
    kinds = portrait.node_kinds(graph(), names)
    net = Network(height=f'{height}px', width='100%', directed=True, bgcolor='#ffffff', cdn_resources='remote')
    for n in names:
        kind = kinds.get(n, 'Person')
        big = n in focus
        net.add_node(n, label=n, color=KIND_COLOR[kind], size=30 if big else 14, borderWidth=3 if big else 1,
                     title=f'{n} — {KIND_VI[kind]}', shape='dot', font={'size': 18 if big else 13})
    for a, rel, b in edges:
        undirected = rel == 'TEAMMATE_OF'
        net.add_edge(a, b, title=REL_VI.get(rel, rel), color=REL_COLOR.get(rel, '#cbd5e1'),
                     arrows='' if undirected else 'to', width=2 if rel != 'TEAMMATE_OF' else 1)
    net.set_options(json.dumps({'physics': {'solver': 'forceAtlas2Based',
                                            'forceAtlas2Based': {'gravitationalConstant': -60, 'springLength': 110},
                                            'stabilization': {'iterations': 300, 'fit': True}},
                                'interaction': {'hover': True, 'tooltipDelay': 80, 'navigationButtons': False}}))
    html = net.generate_html().replace(
        'network = new vis.Network(container, data, options);',
        'network = new vis.Network(container, data, options);'
        # Tabs render while hidden (zero size, animation paused): fit whenever layout settles or the
        # canvas gets its real size, and once more shortly after.
        ' function refit() { if (container.clientWidth > 0) network.fit({animation: false}); }'
        ' network.on("stabilized", refit);'
        ' network.once("stabilizationIterationsDone", function () { network.setOptions({physics: false}); refit(); });'
        ' new ResizeObserver(function () { refit(); setTimeout(refit, 700); setTimeout(refit, 2000); }).observe(container);')
    components.html(html, height=height + 20)
    legend()


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title('⚽ Premier League Graph')
    st.caption('Mạng xã hội bóng đá trích xuất từ báo chí bằng Qwen3-4B, liên kết Wikidata, lưu trên Neo4j.')
    try:
        total, kinds, edge_counts, all_names = counts()
        st.success(f'Neo4j: {total:,} nút · {sum(r["n"] for r in edge_counts):,} cạnh')
    except Exception:
        st.error('Chưa kết nối được Neo4j. Hãy chạy `docker compose up -d`.')
        st.stop()
    llm = client()
    if llm:
        st.success('Mô hình Qwen3-4B: đã kết nối')
    else:
        st.warning('Chưa có endpoint Qwen3-4B (phiên Kaggle). Phần hỏi đáp tạm khoá.')
        if st.button('Thử kết nối lại'):
            st.session_state.client = None
            st.rerun()

tab_overview, tab_portrait, tab_qa = st.tabs(['📊 Tổng quan mạng', '🧑 Chân dung một nút', '💬 Hỏi đáp đa bước'])

# ---------------------------------------------------------------- overview
with tab_overview:
    sna = report(f'sna_{RUN}.json')
    cores = report(f'cores_{RUN}.json')
    c = st.columns(5)
    c[0].metric('Nút', f'{total:,}')
    c[1].metric('Cạnh', f'{sum(r["n"] for r in edge_counts):,}')
    c[2].metric('CLB', f'{kinds["Club"]:,}')
    c[3].metric('Cầu thủ', f'{kinds["Player"]:,}')
    c[4].metric('HLV / người đại diện', f'{kinds["Coach"]:,} / {kinds["Agent"]:,}')

    left, right = st.columns([1.1, 1])
    with left:
        st.subheader('Quan hệ trích từ báo chí')
        frame = pd.DataFrame([{'Quan hệ': REL_VI.get(r['relation'], r['relation']), 'Số cạnh': r['n']}
                              for r in edge_counts if r['relation'] not in portrait.DERIVED])
        st.bar_chart(frame, x='Quan hệ', y='Số cạnh', horizontal=True, height=400, color='#2563eb')
        derived_rows = {r['relation']: r['n'] for r in edge_counts if r['relation'] in portrait.DERIVED}
        st.caption('Quan hệ suy diễn từ mốc thời gian sự nghiệp trên Wikidata: '
                   + ' · '.join(f'{REL_VI[k]} {v:,}' for k, v in derived_rows.items()))
    with right:
        if sna:
            sw = sna['teammate']['small_world']
            st.subheader('Mạng đồng đội là mạng thế giới nhỏ')
            st.caption(f'{sw["nodes"]:,} người · {sw["edges"]:,} cạnh · bậc trung bình {sw["average_degree"]}')
            st.dataframe(pd.DataFrame([
                {'Chỉ số': 'Độ dài đường đi trung bình L', 'Mạng thật': sw['L'], 'Mạng ngẫu nhiên': sw['L_random']},
                {'Chỉ số': 'Hệ số phân cụm C', 'Mạng thật': sw['C'], 'Mạng ngẫu nhiên': sw['C_random']},
            ]), hide_index=True, use_container_width=True)
            m = st.columns(2)
            m[0].metric('σ (sigma)', sw['sigma'], help='σ > 1: thế giới nhỏ (C cao hơn hẳn, L gần bằng mạng ngẫu nhiên)')
            m[1].metric('ω (xấp xỉ)', sw['omega_approx'], help='ω gần 0: thế giới nhỏ')
            comm = sna['teammate']['communities']
            st.subheader('Cộng đồng')
            st.dataframe(pd.DataFrame([{'Thuật toán': k, 'Số cộng đồng': v['communities'], 'Modularity': v['modularity'],
                                        'NMI với CLB chính': v['nmi_with_main_club']} for k, v in comm.items()
                                       if isinstance(v, dict) and 'modularity' in v]),
                         hide_index=True, use_container_width=True)

    if cores:
        t = cores['teammate']
        st.subheader('Lõi mạng (k-core) và hạt nhân cộng đồng')
        k = st.columns(4)
        k[0].metric('k lớn nhất', t['k_max'])
        k[1].metric('Số người trong lõi chính', t['main_core']['size'])
        k[2].metric('Mật độ lõi chính', t['main_core']['density'])
        k[3].metric('Spearman(core, bậc)', t['core_degree_spearman'])
        st.dataframe(pd.DataFrame([{'Cộng đồng (Leiden)': i + 1, 'Kích thước': n.get('community_size'),
                                    'k của hạt nhân': n.get('nucleus_k'), 'Người trong hạt nhân': n.get('nucleus_size'),
                                    'CLB chính': ', '.join(c[0] for c in n.get('main_clubs', [])[:2]),
                                    'Tiêu biểu': ', '.join(n.get('nucleus_members', [])[:5])}
                                   for i, n in enumerate(t.get('leiden_community_nuclei', []))]),
                     hide_index=True, use_container_width=True)
    if sna:
        st.subheader('Nhân vật trung tâm của mạng đồng đội')
        central = sna['teammate']['central']
        cols = st.columns(3)
        for col, (key, title) in zip(cols, [('degree', 'Bậc (số đồng đội)'), ('pagerank', 'PageRank'),
                                            ('betweenness_sampled_300_sources', 'Trung gian (betweenness)')]):
            col.markdown(f'**{title}**')
            col.dataframe(pd.DataFrame(central[key], columns=['Tên', 'Giá trị']), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- portrait
with tab_portrait:
    default = all_names.index('Wayne Rooney') if 'Wayne Rooney' in all_names else 0
    name = st.selectbox('Chọn một cầu thủ, HLV hoặc CLB', all_names, index=default)
    info = portrait.entity(graph(), name)
    rels = portrait.relations(graph(), name)
    derived = portrait.derived_counts(graph(), name)
    role_text = ', '.join(KIND_VI.get(r, r) for r in info['roles']) or 'Nhân vật'
    st.markdown(f'## {name}')
    st.caption(f'{role_text} · Wikidata {info["qid"] or "—"} · tên khác: {", ".join((info["aliases"] or [])[:6])}')

    position = None if info['is_club'] else portrait.social_position(graph(), name)
    c = st.columns(5)
    if info['is_club']:
        c[0].metric('Người từng thi đấu', derived.get('players', 0))
        c[1].metric('Quan hệ trích từ báo', len(rels))
        c[2].metric('Dòng chuyển nhượng đi', derived.get('flow_out', 0))
        c[3].metric('Dòng chuyển nhượng đến', derived.get('flow_in', 0))
        c[4].metric('Bằng chứng (lượt nhắc)', sum(r['evidence'] or 0 for r in rels))
    elif position:
        n = position['people_in_network']
        c[0].metric('Số đồng đội (bậc)', position['degree'], f'hạng {position["degree_rank"]}/{n}', delta_color='off')
        c[1].metric('PageRank', f'{position["pagerank"]:.5f}', f'hạng {position["pagerank_rank"]}/{n}', delta_color='off')
        c[2].metric('Chỉ số lõi k', position['core'], 'lõi chính' if position['in_main_core'] else f'k tối đa = {position["k_max"]}',
                    delta_color='normal' if position['in_main_core'] else 'off')
        comm = position['community']
        c[3].metric('Cộng đồng Leiden', f'#{comm["index"]}' if comm else '—', f'{comm["size"]} người' if comm else None, delta_color='off')
        c[4].metric('HLV đã dẫn dắt / học trò', f'{derived.get("coaches", 0)} / {derived.get("players_coached", 0)}')
    else:
        st.info('Nhân vật này không nằm trong mạng đồng đội (không có dữ liệu sự nghiệp trên Wikidata).')

    left, right = st.columns([1.5, 1])
    with left:
        st.subheader('Mạng quan hệ xung quanh')
        draw(portrait.ego_edges(graph(), name), focus=(name,))
    with right:
        if position and position['community']:
            nuc = position['community']['nucleus']
            st.subheader('Hạt nhân cộng đồng')
            st.caption(f'Lõi k = {nuc["k"]} bên trong cộng đồng, gồm {nuc["size"]} người, tiêu biểu:')
            st.write(', '.join(nuc['members']))
        st.subheader('Quan hệ trích từ báo chí')
        if rels:
            st.dataframe(pd.DataFrame([{'Quan hệ': REL_VI.get(r['relation'], r['relation']) + ('' if r['outgoing'] else ' (bị)'),
                                        'Đối tượng': r['other'], 'Bằng chứng': r['evidence'],
                                        'Từ': r['first_seen'], 'Đến': r['last_seen']} for r in rels]),
                         hide_index=True, use_container_width=True, height=300)
    st.subheader('Bằng chứng mới nhất')
    for e in portrait.evidence(graph(), name):
        st.markdown(f'**{REL_VI.get(e["relation"], e["relation"])} → {e["other"]}** · {e["published"] or ""} · '
                    f'[{e["title"] or "bài báo"}]({e["url"]})')
        st.markdown(f'<div class="quote">“{e["quote"]}”</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- question answering
with tab_qa:
    examples = ['HLV nào từng huấn luyện các đồng đội của Wayne Rooney?',
                'Trong số cầu thủ từng khoác áo Chelsea, ai từng được José Mourinho huấn luyện?',
                'Những ai từng lên tiếng chỉ trích Mikel Arteta?',
                'Có bao nhiêu cầu thủ từng chơi cho cả Arsenal lẫn Chelsea?',
                'Hạt nhân của mạng đồng đội gồm những ai?',
                'Harry Kane và Mohamed Salah nối với nhau qua những ai?']
    pick = st.selectbox('Câu hỏi mẫu', ['— tự nhập —'] + examples)
    question = st.text_input('Câu hỏi', value='' if pick.startswith('—') else pick,
                             placeholder='Ví dụ: Các HLV từng huấn luyện Bukayo Saka đã dẫn dắt những CLB nào?')
    go = st.button('Trả lời', type='primary', disabled=llm is None or not question)
    if go:
        with st.spinner('Đang liên kết thực thể, lập kế hoạch và truy vấn đồ thị…'):
            result = ask(llm, graph(), question)
        st.session_state.qa = result
    result = st.session_state.get('qa')
    if result:
        for note in result.get('notes', []):
            st.info(note)
        params = result.get('params') or {}
        if result.get('stage') != 'linking':
            st.markdown('**① Thực thể nhận diện:** ' + (', '.join(f'`{v}`' for v in params.values()) or 'không có (câu hỏi toàn mạng)'))
            st.markdown('**② Hướng xử lý:** ' + ('công cụ phân tích mạng' if result.get('route') == 'analytics'
                                                 else 'lập kế hoạch truy vấn nhiều bước trên đồ thị'))
        if not result['ok']:
            st.error(result['message'])
        elif result.get('tool'):
            st.markdown(f'**③ Công cụ:** `{result["tool"]}` {result["args"] or ""}')
        if result.get('plan'):
            st.markdown('**③ Kế hoạch và số thực thể sau mỗi bước:**')
            previous = None
            for step in result.get('trace') or []:
                start = params.get(step['from'], step['from'])
                head = f'<b>{start}</b>' if step['from'] != previous else '&nbsp;&nbsp;↳'
                previous = step['from']
                st.markdown(f'<div class="step">{head} → <code>{step["hop"]}</code> '
                            f'({step["meaning"]}): <b>{step["count"]}</b> thực thể, ví dụ {", ".join(step["sample"][:3])}</div>',
                            unsafe_allow_html=True)
            checks = result.get('checks') or []
            fast = result.get('fast') or {}
            vi = lambda problems: '; '.join(f'{p[:2]} {CHECK_VI.get(p[:2], p)}' for p in problems)
            if result.get('escalated'):
                st.warning('④ Kiểm tra tự động phát hiện vấn đề ở kế hoạch nhanh ('
                           + vi(fast.get('problems') or []) + ') → đã lập lại kế hoạch bằng chế độ suy nghĩ (thinking).')
            st.markdown('**④ Kiểm tra tự động:** ' + ('✓ dùng đủ thực thể · ✓ vai trò · ✓ từ khoá quan hệ · ✓ loại đáp án · ✓ có kết quả'
                                                      if not checks else '⚠ ' + vi(checks)))
            with st.expander('Truy vấn Cypher được biên dịch từ kế hoạch'):
                st.code(result.get('query') or '', language='cypher')
                st.json(result['plan'])
        if result['ok']:
            st.markdown('**⑤ Trả lời (mỗi dòng gắn bằng chứng từ báo nếu có):**')
            total = result.get('total')
            if total and total > len(result.get('rows') or []):
                st.caption(f'Có {total} kết quả, hiển thị {len(result["rows"])} kết quả đầu (theo thứ tự tên).')
            st.markdown(result['answer'])
            if result.get('edges'):
                st.markdown('**Đồ thị con giải thích câu trả lời**')
                draw(result['edges'], focus=tuple(params.values()), height=500)
