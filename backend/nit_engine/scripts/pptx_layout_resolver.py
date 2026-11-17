"""Read effectively displayed PPTX shapes, including inherited layout text.

Layout/master placeholders are NOT rendered as literal slide text; skip them.
Non-placeholder inherited text is preserved as an *unverified visible candidate*
for downstream visual QA. No arbitrary notes or hidden text is included.
"""
from collections import Counter

def compact(s):
    return ' '.join(str(s or '').split())

def hidden(shape):
    try:
        nodes=shape.element.xpath('.//p:cNvPr')
        return any(n.get('hidden') in ('1','true') for n in nodes)
    except Exception:return False

def bbox(shape,width,height):
    try:
        return [round(float(shape.left)/width,5),round(float(shape.top)/height,5),
                round(float(shape.left+shape.width)/width,5),round(float(shape.top+shape.height)/height,5)]
    except Exception:return None

def normalized_wingdings_text(shape):
    """Normalize only run-font-confirmed Wingdings U+F0E0 to a right arrow.

    Inferred/default fonts do not qualify. Every substitution records
    run position and source glyph so the teacher can audit the decision.
    """
    if not getattr(shape,"has_text_frame",False):
        return getattr(shape,"text","") or "",[]
    lines=[];provenance=[]
    for pi,paragraph in enumerate(shape.text_frame.paragraphs):
        chunks=[]
        for ri,run in enumerate(paragraph.runs):
            original=run.text or ""
            font_name=(run.font.name or "").strip().casefold()
            if font_name=="wingdings" and "\uf0e0" in original:
                for offset,ch in enumerate(original):
                    if ch=="\uf0e0":
                        provenance.append({"paragraph_index":pi,"run_index":ri,
                            "offset_in_run":offset,"source_char":"U+F0E0",
                            "normalized_char":"→","font":"Wingdings",
                            "rule":"pptx_explicit_run_font_v1"})
                chunks.append(original.replace("\uf0e0","→"))
            else:
                chunks.append(original)
        lines.append("".join(chunks) if chunks else paragraph.text)
    return "\n".join(lines),provenance


def paragraphs(shape):
    if not getattr(shape,'has_text_frame',False):return []
    result=[]
    for i,par in enumerate(shape.text_frame.paragraphs):
        if not compact(par.text):continue
        runs=[]
        for r in par.runs:
            if not r.text:continue
            c=None
            try:
                if r.font.color and r.font.color.type and r.font.color.rgb:c=str(r.font.color.rgb)
            except (AttributeError,ValueError):pass
            display=r.text.replace('\uf0e0','→') if (r.font.name or '').casefold()=='wingdings' else r.text
            runs.append({'text':display,'raw_text':r.text,'font':r.font.name,
                         'bold':r.font.bold,'italic':r.font.italic,'color_rgb':c,
                         'hyperlink':getattr(r.hyperlink,'address',None)})
        result.append({'text':''.join(r['text'] for r in runs) if runs else par.text,'level':par.level,'runs':runs})
    return result

def is_media_control(shape):
    """PowerPoint play/audio/video controls are UI, not learning visual evidence.

    Detect media relationship/action in the shape tree; a visually similar
    decorative icon remains unverified until the real slide is inspected.
    """
    try:
        xml=shape.element.xml.lower()
        return ('ppaction://media' in xml or
                'audiofile' in xml or 'videofile' in xml or
                'mediarid' in xml or 'mediatrim' in xml)
    except (AttributeError, ValueError):
        return False


def extract_visible(slide,slide_index,width,height):
    blocks=[]; stats=Counter();rects=[]
    def emit(shapes,layer,include_placeholders=False):
        for sh in shapes:
            if hidden(sh):continue
            if sh.shape_type==6: # groups
                emit(sh.shapes,layer,include_placeholders);continue
            if layer!='slide' and sh.is_placeholder:continue
            if layer=='slide' and sh.is_placeholder and not compact(getattr(sh,'text','')):
                continue
            box=bbox(sh,width,height)
            base={'id':f'b{len(blocks):04d}','origin':'native' if layer=='slide' else 'inherited_layout',
                  'source_type':{'slide':'slide_body','layout':'slide_layout','master':'slide_master'}[layer],
                  'source_page':slide_index,'object_id':sh.shape_id,'bbox':box,
                  'requires_visual_confirmation':layer!='slide'}
            normalized_text,arrow_provenance=normalized_wingdings_text(sh)
            if layer!='slide' and getattr(sh,'has_text_frame',False) and compact(normalized_text):
                txt=compact(normalized_text)
                if any(b.get('text')==txt for b in blocks):continue
                blocks.append({**base,'type':'text','text':txt,'raw_text':sh.text,
                               'paragraphs':paragraphs(sh),'glyph_normalizations':arrow_provenance,
                               'visible_candidate':True})
                stats['inherited_text_blocks']+=1
                continue
            if layer!='slide':continue
            if sh.has_table:
                rows=[[compact(c.text) for c in row.cells] for row in sh.table.rows]
                blocks.append({**base,'type':'table','rows':rows,'table_shape':[len(rows),max((len(r) for r in rows),default=0)]})
                stats['tables']+=1
            if getattr(sh,'has_chart',False):
                data={'series':[],'category_labels':[]}
                try:
                    chart=sh.chart
                    try:data['category_labels']=[compact(c.label) for c in chart.plots[0].categories]
                    except (ValueError,AttributeError,IndexError):pass
                    for serie in chart.series:
                        vals=[]
                        for v in serie.values:
                            try:vals.append(float(v) if v is not None else None)
                            except (ValueError,TypeError):vals.append(None)
                        data['series'].append({'name':compact(serie.name),'values':vals})
                except Exception:data['extraction_error']='native_chart_data_unavailable'
                blocks.append({**base,'type':'chart','chart_data':data,'text':'',
                               'verification':'unverified_chart_data'})
                stats['charts']+=1
            if getattr(sh,'has_text_frame',False) and compact(normalized_text):
                blocks.append({**base,'type':'text','text':compact(normalized_text),'raw_text':sh.text,
                               'paragraphs':paragraphs(sh),'glyph_normalizations':arrow_provenance})
            if sh.shape_type==13 or 'PICTURE' in str(sh.shape_type):
                if is_media_control(sh):
                    stats['media_control_images']+=1
                    # It is not evidence of an educational image/diagram.
                else:
                    stats['images']+=1
                    if box:rects.append(box)
            if 'FREEFORM' in str(sh.shape_type) or 'AUTO_SHAPE' in str(sh.shape_type):stats['shapes']+=1
    # For this task, inherited layout text is visible without being slide-owned.
    emit(slide.shapes,'slide')
    emit(slide.slide_layout.shapes,'layout')
    # Master text is only read when it is not a placeholder. It is unverified and
    # NEVER auto-eligible for student coaching.
    emit(slide.slide_layout.slide_master.shapes,'master')
    stats['image_area_ratio']=round(min(sum(max((x[2]-x[0])*(x[3]-x[1]),0) for x in rects),1),4)
    return blocks,dict(stats)
