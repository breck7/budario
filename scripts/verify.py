import json,re,sys
from pathlib import Path
from urllib.parse import unquote
from bs4 import BeautifulSoup
root=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(root/'scripts'))
from import_blog import extract_body
posts=json.loads((root/'archive.json').read_text());errors=[]
for p in posts:
 original=extract_body((root/'originals'/p['original']).read_text())
 for sup in original.select('sup'):
  if sup.get_text(strip=True).isdigit():sup.replace_with('['+sup.get_text(strip=True)+']')
 page=BeautifulSoup((root/(p['slug']+'.html')).read_text(),'html.parser')
 norm=lambda s:re.sub(r'\s+','',s)
 expected=norm(p['subtitle']+original.get_text());actual=norm(page.select_one('.prose').get_text())
 if expected!=actual:
  from difflib import SequenceMatcher
  m=SequenceMatcher(None,expected,actual,autojunk=False)
  for op,i,j,k,l in m.get_opcodes():
   if op!='equal':errors.append((p['slug'],'text',op,expected[max(0,i-30):j+30],actual[max(0,k-30):l+30]))
for f in root.glob('*.html'):
 page=BeautifulSoup(f.read_text(),'html.parser')
 if len(page.select('h1'))!=1:errors.append((f.name,'h1'))
 if len(page.select('main,[role=main]'))!=1:errors.append((f.name,'main'))
 for node in page.select('[href],[src]'):
  url=node.get('href',node.get('src',''))
  if url.startswith(('http:','https:','data:','mailto:')):continue
  target,_,fragment=url.partition('#');path=root/unquote(target.split('?')[0]) if target else f
  if not path.exists():errors.append((f.name,'missing',url));continue
  if fragment and path.suffix=='.html':
   other=page if path==f else BeautifulSoup(path.read_text(),'html.parser')
   if not other.find(id=unquote(fragment)):errors.append((f.name,'anchor',url))
archive=BeautifulSoup((root/'index.html').read_text(),'html.parser')
assert len(archive.select('.post-row'))==len(posts)
rows=json.loads((root/'search.json').read_text());assert len(rows)==len(posts)
for row in rows:
 for label in ['Skip to content','← All writing','View source']:
  if label in row['text']:errors.append(('search',label))
print('\n'.join(str(e) for e in errors[:30]));print(len(posts),'articles;',len(list(root.glob('*.html'))),'pages;',len(errors),'issues')
sys.exit(bool(errors))
