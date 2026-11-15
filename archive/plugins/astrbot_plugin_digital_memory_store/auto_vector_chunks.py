"""长原话的完整分段编码；仅生成候选向量，不改变来源文本。"""
from .engine.e419.dynamic_index import _unit


def encode_complete_source(encoder,text,max_parts=128):
    """长度错误时递归拆分，所有片段成功后才能生成平均候选向量。"""
    if not isinstance(text,str) or not text:raise ValueError('empty source')
    pending=[text];vectors=[]
    while pending:
        piece=pending.pop()
        try:
            result=encoder.encode_documents([piece])
            if not isinstance(result,(list,tuple)) or len(result)!=1:raise ValueError('embedding shape')
            vectors.append(_unit(result[0]))
        except ValueError:
            if len(piece)<16 or len(vectors)+len(pending)+2>max_parts:raise
            middle=len(piece)//2
            pending.extend((piece[middle:],piece[:middle]))
    if len(vectors)<2:raise ValueError('source was not split')
    dimension=len(vectors[0])
    if any(len(vector)!=dimension for vector in vectors):raise ValueError('embedding dimension')
    return _unit([sum(vector[i] for vector in vectors) for i in range(dimension)]),len(vectors)


def add_chunk_notice(db,scope,model_key,ids,payload):
    """向回复模型提示所选线索采用了完整分段平均向量。"""
    for seq in ids:
        if db.execute('''SELECT 1 FROM dl_auto_vector_chunks v JOIN dl_capture c
            ON c.scope=v.scope AND c.seq=v.seq AND c.content_sha=v.content_sha
            WHERE v.scope=? AND v.model_key=? AND v.seq=?''',(scope,model_key,seq)).fetchone():
            payload['检索提示']='部分原话由分段向量定位，细节相似度可能受平均影响；请以完整原话为准'
            break
