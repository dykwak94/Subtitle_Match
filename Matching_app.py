import streamlit as st
import pandas as pd
import pysrt
import re
import ast
import os

# -----------------------------------------------------------------------------
# Helper to parse an uploaded SRT into a DataFrame, filtering by language
# -----------------------------------------------------------------------------
def parse_srt_to_df(uploaded_file, lang):
    # Ensure it’s .srt
    if not uploaded_file.name.lower().endswith('.srt'):
        st.error(f"{uploaded_file.name} is not an SRT file.")
        return None
    try:
        raw = uploaded_file.getvalue().decode('utf-8', errors='ignore')
        subs = pysrt.from_string(raw)
    except Exception as e:
        st.error(f"Failed to parse {uploaded_file.name}: {e}")
        return None

    rows = []
    for sub in subs:
        text = sub.text.replace('\n', ' ')
        # filter undesired language
        has_kor = bool(re.search(r'[\uac00-\ud7a3]', text))
        if lang == 'en' and has_kor:
            continue
        if lang == 'ko' and not has_kor:
            continue
        # format times as HH:MM:SS.mmm
        def fmt(t):
            return f"{t.hours:02d}:{t.minutes:02d}:{t.seconds:02d}.{int(t.milliseconds):03d}"
        rows.append({
            "start_time": fmt(sub.start),
            "end_time":   fmt(sub.end),
            "text":       text
        })
    return pd.DataFrame(rows)

# -----------------------------------------------------------------------------
# Streamlit UI
# -----------------------------------------------------------------------------
from PIL import Image

logo = Image.open("./1cdc3c07-14ea-43d0-a328-77c4f8d8688f.png")
col1, col2 = st.columns([1,8])
with col1:
    st.image("logo@4x.png", width=50)   # source is 200×200, downsized to 50×50
with col2:
    st.markdown("## 🔗 Subtitle Matching Web App")

st.write("---")  # optional separator

# 1) Upload both SRTs
eng_file = st.file_uploader("Upload English subtitles (.srt)", type=["srt"])
kor_file = st.file_uploader("Upload Korean subtitles (.srt)", type=["srt"])

if eng_file and kor_file:
    # 2) Parse into DataFrames
    eng_df = parse_srt_to_df(eng_file, lang='en')
    kor_df = parse_srt_to_df(kor_file, lang='ko')
    if eng_df is None or kor_df is None:
        st.stop()

    # convert to timedeltas for later
    eng_df['start_time'] = pd.to_timedelta(eng_df['start_time'])
    eng_df['end_time']   = pd.to_timedelta(eng_df['end_time'])
    kor_df['start_time'] = pd.to_timedelta(kor_df['start_time'])
    kor_df['end_time']   = pd.to_timedelta(kor_df['end_time'])

    # store in session_state
    st.session_state.eng_df = eng_df
    st.session_state.kor_df = kor_df

    st.success("Files loaded & cleaned.")

    # 3) Show top-15 when asked
    if st.button("Show top 15 of each"):
        def preview_df(df):
            d = df.copy().reset_index(drop=True)
            # turn Timedelta → string like "00:00:03.475000", then chop off the last three zeros
            d['start_time'] = d['start_time'].astype(str).str.split(' ').str[-1].str[:-3]
            d['end_time']   = d['end_time'].astype(str).str.split(' ').str[-1].str[:-3]
            return d[['start_time','end_time','text']]

        st.subheader("▶ English")
        st.dataframe(preview_df(st.session_state.eng_df).head(15))

        st.subheader("▶ Korean")
        st.dataframe(preview_df(st.session_state.kor_df).head(15))

    # 4) Manual matching inputs
    st.markdown("### Manual match by index tuples")
    eng_idx_txt = st.text_input("English indices (e.g. (2,4,6))")
    kor_idx_txt = st.text_input("Korean indices (e.g. (3,5,6))")
    if st.button("Show manual match"):
        try:
            eng_idx = list(ast.literal_eval(eng_idx_txt))
            kor_idx = list(ast.literal_eval(kor_idx_txt))
            sel_eng = eng_df.iloc[eng_idx]
            sel_kor = kor_df.iloc[kor_idx]
            combined = []
            for e, k in zip(sel_eng.to_dict('records'), sel_kor.to_dict('records')):
                interval = k['start_time'] - e['start_time']
                combined.append({
                    "start_time_ENG": str(e['start_time']),
                    "ENG text":       e['text'],
                    "start_time_KOR": str(k['start_time']),
                    "KOR text":       k['text'],
                    "interval":       str(interval)
                })
            st.dataframe(pd.DataFrame(combined))
        except Exception as e:
            st.error(f"Invalid indices or parse error: {e}")

    # 6) Shift input
    shift_sec = st.number_input(
        "Shift Korean subtitles by (seconds)",
        min_value=-3600.0,
        max_value=3600.0,
        value=0.0,
        step=0.1,
        format="%.3f"  # show three decimal places
    )
    if st.button("Apply shift"):
        k2 = kor_df.copy()
        k2['start_time'] -= pd.to_timedelta(shift_sec, unit='s')
        st.session_state.kor_shifted = k2
        st.info(f"Korean subtitles shifted by {shift_sec} seconds")
        #st.dataframe(k2.head(15))

    # 8) Automatic matching
    if 'kor_shifted' in st.session_state and st.button("Run automatic matching"):
    # 1) Prepare & rename for clarity
        e2 = (
            st.session_state.eng_df
            .sort_values('start_time')
            .reset_index(drop=True)
            .rename(columns={'text':'Eng_text','start_time':'start_time_ENG'})
        )
        k2 = (
            st.session_state.kor_shifted
            .sort_values('start_time')
            .reset_index(drop=True)
            .rename(columns={'text':'Kor_text','start_time':'start_time_KOR'})
        )

        with st.spinner("Matching..."):
            # 2) nearest‐time merge
            matched = pd.merge_asof(
                left=e2, right=k2,
                left_on='start_time_ENG', right_on='start_time_KOR',
                direction='nearest', tolerance=pd.Timedelta(seconds=1)
            )

            # 3) build the matched pairs DataFrame
            matched_pairs = matched[['Eng_text','Kor_text']].copy()

            # 4) find all Korean rows that never appeared in matched['start_time_KOR']
            matched_kor_times = matched['start_time_KOR'].dropna().unique()
            unmatched_kor = k2.loc[~k2['start_time_KOR'].isin(matched_kor_times), 'Kor_text']

            # 5) turn them into a two‐column frame with an empty Eng_text
            unmatched_kor_df = pd.DataFrame({
                'Eng_text': [''] * len(unmatched_kor),
                'Kor_text': unmatched_kor.values
            })

            # 6) concatenate matched + unmatched
            final = pd.concat([matched_pairs, unmatched_kor_df], ignore_index=True)

            # 7) save & stash
            final.to_csv('matched_subtitles.csv', index=False)
            st.session_state.matched = final

        st.success("Automatic matching done.")
        st.subheader("Top 30 matches + unmatched Korean at bottom")
        st.dataframe(st.session_state.matched.head(30))
    # 9) Download or delete
    if 'matched' in st.session_state:
        col1, col2 = st.columns(2)
        with col1:
            with open('matched_subtitles.csv','rb') as f:
                st.download_button(
                    "Download matched CSV", 
                    f, 
                    file_name='matched_subtitles.csv', 
                    mime='text/csv'
                )
        with col2:
            if st.button("Not good (delete file)"):
                try:
                    os.remove('matched_subtitles.csv')
                except FileNotFoundError:
                    pass
                st.session_state.pop('matched')
                st.info("Matched file deleted.")
st.markdown("---")  
st.markdown("Created by Davis K")  