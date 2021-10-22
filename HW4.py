import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
st.title ("Homework 4")  #question 1

st.markdown ("[Zhiyang Jiang](https://github.com/Bill663)")  #question 2
st.markdown ("[kaihao Zhang](https://github.com/tyb0v0)")  #question 2

file = st.file_uploader("upload file here:", type = ["csv"])  #question 3

if file is not None:
    df = pd.read_csv(file)   #question 4
    df = df.applymap(lambda x: np.nan if x=="" else x)
    df

    def can_be_numeric(c):    #quesiton 6
        try:
            pd.to_numeric(df[c])
            return True
        except:
            return False

    good_cols = [c for c in df.columns if can_be_numeric(c)]   #question 7
    
    df[good_cols] = df[good_cols].apply(pd.to_numeric, axis = 0)
    
    x_axis = st.selectbox("Choose an x_value",good_cols)  #question 8
    y_axis = st.selectbox("Choose an y_value",good_cols)

    values = st.slider("Select a range of values",0, len(df.index)-1,(60,len(df.index)-1))

    st.write(f"plotting ({x_axis},{y_axis}) for rows {values}")
    val = list(values)
    
    row_val = df.loc[val[0]:val[1]]

    my_chart = alt.Chart(row_val).mark_point().encode(x = x_axis, y = y_axis)
    st.altair_chart(my_chart)
    
    st.download_button(
        label = "Download selected values as CSV",
        data = row_val.to_csv().encode('utf-8'),
        file_name = 'Selected_values.csv',
        mime='text/csv'
        )
    