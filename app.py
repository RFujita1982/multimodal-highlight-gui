# =========================================================
# Document AI Engine — α ver. (Multimodal Highlight GUI)
# FLE internal / Streamlit front-end for the Dify chatflow
# "Multimodal_Aug..." (ECR -> manual PDF auto-highlight)
#
# 必要な secrets（.streamlit/secrets.toml）:
#   ACCESS_CODE   = "社内配布のアクセスコード"
#   DIFY_API_KEY  = "app-xxxxxxxxxxxxxxxx"   # MultimodalアプリのAPIキー
#   DIFY_BASE_URL = "https://api.dify.ai/v1" # 省略可（デフォルト）
#
# ロゴ: リポジトリ直下に assets/fle_logo.png を置く
# 依存: requirements.txt 参照（streamlit, requests）
# =========================================================

import json
import time
import requests
import streamlit as st

# ---------- 表示テキスト（ここを書き換えれば文言変更できる） ----------
APP_TITLE = "Document AI Engine — α ver."
APP_SUBTITLE = "ECR → Manual Highlight Automation"
GATE_CAPTION = "Internal access — enter the access code provided to you."
GATE_FOOTER = "FLE internal use only. Do not share this URL or code."
WELCOME_MSG = "Attach the manual PDF and the ECR, then ask where to change."
CHAT_PLACEHOLDER = "Ask about the manual… (e.g. Based on ECR, please tell me where to change)"
TITLE_COLOR = "#C8102E"  # FLEロゴの赤に合わせる。好みで調整
LOGO_PATH = "assets/fle_logo.png"

DIFY_BASE_URL = st.secrets.get("DIFY_BASE_URL", "https://api.dify.ai/v1")
DIFY_API_KEY = st.secrets["DIFY_API_KEY"]
ACCESS_CODE = st.secrets["ACCESS_CODE"]
# Difyのファイル配信URLは /v1 を除いたホストからの相対パスで返る
DIFY_HOST = DIFY_BASE_URL.rsplit("/v1", 1)[0]

st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")

# ---------- session state ----------
ss = st.session_state
ss.setdefault("authed", False)
ss.setdefault("conversation_id", "")
ss.setdefault("messages", [])          # [{role, content, files:[{name,url}]}]
ss.setdefault("manual_file_id", None)  # 同一会話で使い回すmanual_pdfのupload_file_id
ss.setdefault("manual_file_name", None)
ss.setdefault("user_id", f"fle-{int(time.time())}")


# ---------- Dify API helpers ----------
def dify_upload(uploaded_file) -> str:
    """StreamlitのUploadedFileをDifyへアップロードし upload_file_id を返す"""
    r = requests.post(
        f"{DIFY_BASE_URL}/files/upload",
        headers={"Authorization": f"Bearer {DIFY_API_KEY}"},
        files={"file": (uploaded_file.name, uploaded_file.getvalue(),
                        uploaded_file.type or "application/pdf")},
        data={"user": ss.user_id},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["id"]


def dify_chat_stream(query: str, manual_file_id: str, ecr_file_id: str | None):
    """chat-messages(SSE)を叩き、(答えテキストのgenerator, 添付ファイルlist) を返す。
    添付ファイルlistはストリーム消費後に埋まる点に注意。"""
    payload = {
        "inputs": {
            "manual_pdf": {
                "type": "document",
                "transfer_method": "local_file",
                "upload_file_id": manual_file_id,
            }
        },
        "query": query,
        "response_mode": "streaming",
        "conversation_id": ss.conversation_id,
        "user": ss.user_id,
        "files": (
            [{
                "type": "document",
                "transfer_method": "local_file",
                "upload_file_id": ecr_file_id,
            }] if ecr_file_id else []
        ),
    }
    out_files: list[dict] = []

    def gen():
        with requests.post(
            f"{DIFY_BASE_URL}/chat-messages",
            headers={
                "Authorization": f"Bearer {DIFY_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=600,
        ) as r:
            r.raise_for_status()
            for raw in r.iter_lines():
                if not raw or not raw.startswith(b"data: "):
                    continue
                try:
                    ev = json.loads(raw[6:].decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                et = ev.get("event")
                if et == "message":
                    ss.conversation_id = ev.get("conversation_id", ss.conversation_id)
                    yield ev.get("answer", "")
                elif et == "message_file":
                    url = ev.get("url", "")
                    if url.startswith("/"):
                        url = DIFY_HOST + url
                    out_files.append({"name": url.split("?")[0].split("/")[-1], "url": url})
                elif et == "error":
                    yield f"\n\n:red[**Error:** {ev.get('message', 'unknown error')}]"

    return gen, out_files


def fetch_file_bytes(url: str) -> bytes | None:
    """焼きPDF等をダウンロードボタン用に取得（失敗したらNone→リンク表示にフォールバック）"""
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


# ---------- ゲート画面 ----------
def render_gate():
    st.write("")
    st.write("")
    # 中央上部にFLEロゴ
    _, mid, _ = st.columns([2, 1, 2])
    with mid:
        try:
            st.image(LOGO_PATH, width=140)
        except Exception:
            pass  # ロゴ未配置でも動くように
    st.markdown(
        f"<h1 style='text-align:center;'>{APP_TITLE}</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<p style='text-align:center;color:#6b6b6b;'>{GATE_CAPTION}</p>",
        unsafe_allow_html=True,
    )
    _, form_col, _ = st.columns([1, 2, 1])
    with form_col:
        with st.container(border=True):
            code = st.text_input("Access code", type="password")
            if st.button("Enter"):
                if code == ACCESS_CODE:
                    ss.authed = True
                    st.rerun()
                else:
                    st.error("Invalid access code.")
        st.markdown(
            f"<p style='color:#6b6b6b;'>{GATE_FOOTER}</p>",
            unsafe_allow_html=True,
        )


# ---------- メイン画面 ----------
def render_main():
    with st.sidebar:
        try:
            st.image(LOGO_PATH, width=110)
        except Exception:
            pass
        st.markdown("### FLE Global Inc.")
        if st.button("Reset conversation", use_container_width=True):
            ss.conversation_id = ""
            ss.messages = []
            ss.manual_file_id = None
            ss.manual_file_name = None
            st.rerun()

    st.markdown(
        f"<h3 style='color:{TITLE_COLOR};margin-bottom:0;'>{APP_TITLE}</h3>",
        unsafe_allow_html=True,
    )
    st.markdown(f"#### {APP_SUBTITLE}")
    st.divider()
    st.info(WELCOME_MSG)

    # --- 入力ファイル ---
    c1, c2 = st.columns(2)
    with c1:
        manual_up = st.file_uploader("Manual PDF", type=["pdf"], key="manual_up")
    with c2:
        ecr_up = st.file_uploader("ECR PDF", type=["pdf"], key="ecr_up")
    if ss.manual_file_name and not manual_up:
        st.caption(f"Manual in use for this conversation: **{ss.manual_file_name}**")

    # --- 履歴表示 ---
    for m in ss.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            for f in m.get("files", []):
                st.markdown(f"📎 [{f['name']}]({f['url']})")

    # --- 送信 ---
    query = st.chat_input(CHAT_PLACEHOLDER)
    if not query:
        return

    # manual_pdf は会話の最初に必須。以降は使い回し
    if manual_up is not None:
        with st.spinner("Uploading manual PDF…"):
            ss.manual_file_id = dify_upload(manual_up)
            ss.manual_file_name = manual_up.name
    if ss.manual_file_id is None:
        st.warning("Please attach the manual PDF first.")
        return

    ecr_file_id = None
    if ecr_up is not None:
        with st.spinner("Uploading ECR PDF…"):
            ecr_file_id = dify_upload(ecr_up)

    ss.messages.append({"role": "user", "content": query, "files": []})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        gen, out_files = dify_chat_stream(query, ss.manual_file_id, ecr_file_id)
        answer = st.write_stream(gen())
        file_meta = []
        for f in out_files:
            file_meta.append(f)
            data = fetch_file_bytes(f["url"])
            if data:
                st.download_button(
                    f"⬇️ {f['name']}", data=data,
                    file_name=f["name"], mime="application/pdf",
                    key=f"dl_{len(ss.messages)}_{f['name']}",
                )
            else:
                st.markdown(f"📎 [{f['name']}]({f['url']})")

    ss.messages.append(
        {"role": "assistant", "content": answer, "files": file_meta}
    )


# ---------- entry ----------
if not ss.authed:
    render_gate()
else:
    render_main()
