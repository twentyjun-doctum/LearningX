"""Server-side signed labels. The host must authenticate reviewers and keep key secret."""
import hashlib,hmac,json,os
def canonical(item):
 return json.dumps(item,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')
def payload(item,source_sha):
 return {'source_sha256':source_sha,'page':item.get('page'),'reviewer_id':item.get('reviewer_id'),
         'visual_required':item.get('visual_required'),'text_quality_acceptable':item.get('text_quality_acceptable'),
         'ocr_required':item.get('ocr_required'),'full_reference_text':item.get('full_reference_text')}
def sign_label(item,source_sha,authenticated_reviewer_id,secret):
 if len(secret)<32 or not authenticated_reviewer_id:raise PermissionError('Authenticated reviewer and server signing secret required')
 if item.get('reviewer_id')!=authenticated_reviewer_id or item.get('review_status')!='faculty_verified':
  raise PermissionError('Reviewer mismatch or unverified')
 return hmac.new(secret.encode(),canonical(payload(item,source_sha)),hashlib.sha256).hexdigest()
def is_signed(item,source_sha,secret):
 signature=item.get('label_signature')
 if not signature or len(secret)<32 or item.get('review_status')!='faculty_verified' or not item.get('reviewer_id'):return False
 expected=hmac.new(secret.encode(),canonical(payload(item,source_sha)),hashlib.sha256).hexdigest()
 return hmac.compare_digest(signature,expected)
