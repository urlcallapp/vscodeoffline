import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import falcon
from falcon import testing
import pytest
import requests

import server
import sync
import vsc

VSIX = 'Microsoft.VisualStudio.Services.VSIXPackage'
SIG = 'Microsoft.VisualStudio.Services.VsixSignature'

def extension(name='sample.tool', platform=None):
    version = {'version':'1.0.0','files':[], 'properties':[]}
    if platform:
        version['targetPlatform'] = platform
    return {'identity':name,'extensionId':name+'-id','extensionName':name.split('.')[-1], 'publisher':{'publisherName':name.split('.')[0]}, 'displayName':name,'recommended':True,'versions':[version], 'stats':{'install':1}}

@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(vsc, 'ARTIFACTS', str(tmp_path))
    monkeypatch.setattr(vsc, 'ARTIFACTS_INSTALLERS', str(tmp_path/'installers'))
    monkeypatch.setattr(vsc, 'ARTIFACTS_EXTENSIONS', str(tmp_path/'extensions'))
    (tmp_path/'extensions').mkdir();(tmp_path/'installers').mkdir()
    return tmp_path

@pytest.fixture
def gallery(artifacts):
    return server.VSCGallery(start_worker=False)

def client(gallery):
    app = falcon.App();app.add_route('/query',gallery)
    return testing.TestClient(app)

def query(c, criteria=None, **kwargs):
    return c.simulate_post('/query',json={'filters':[dict(criteria=criteria or [], **kwargs)], 'flags':0})

def test_empty_filters_are_bad_request(gallery):
    assert client(gallery).simulate_post('/query',json={'filters':[],'flags':0}).status_code == 400

@pytest.mark.parametrize('body',[None,[],{'filters':[None]},{'filters':[{'criteria':'wrong'}]},{'filters':[{'criteria':[1]}]}])
def test_bad_payload_is_not_500(gallery,body):
    assert client(gallery).simulate_post('/query',json=body).status_code in (400,415)

def test_paging_and_count(gallery):
    gallery.extensions = {str(i): extension('sample.tool'+str(i)) for i in range(3)}
    response = query(client(gallery),pageNumber=2,pageSize=1)
    assert response.status_code == 200
    result=response.json['results'][0]
    assert len(result['extensions']) == 1
    assert result['resultMetadata'][0]['metadataItems'][0]['count'] == 3

@pytest.mark.parametrize('paging',[{'pageNumber':0},{'pageSize':0},{'pageSize':1001}])
def test_invalid_paging(gallery,paging):
    assert query(client(gallery),**paging).status_code == 400

def test_explicit_missing_name_is_empty(gallery):
    gallery.extensions = {'sample.tool':extension()}
    assert gallery._apply_criteria([{'filterType':7,'value':'missing.tool'}]) == []

def test_multiple_names_are_or_and_text_intersects(gallery):
    gallery.extensions={n:extension(n) for n in ['sample.one','sample.two','other.three']}
    result=gallery._apply_criteria([{'filterType':7,'value':'sample.one'},{'filterType':7,'value':'sample.two'},{'filterType':10,'value':'two'}])
    assert [e['identity'] for e in result] == ['sample.two']

def test_deleted_extension_is_removed(gallery,artifacts):
    folder=artifacts/'extensions/sample.tool';folder.mkdir();manifest=folder/'latest.json';manifest.write_text(json.dumps(extension()))
    gallery.update_state();assert 'sample.tool' in gallery.extensions
    manifest.unlink();gallery.update_state();assert gallery.extensions == {}

def test_paths_use_public_mount_not_absolute_disk(gallery,artifacts):
    e=extension(platform='linux-x64')
    e['versions'][0]['files']=[{'assetType':SIG,'source':'https://upstream.invalid/signature'}]
    result=gallery.process_loaded_extension(e,str(artifacts/'extensions/sample.tool'))
    uri=result['versions'][0]['assetUri']
    assert uri == vsc.URLROOT+'/artifacts/extensions/sample.tool/1.0.0/linux-x64'
    assert result['versions'][0]['files'][0]['source'] == uri+'/'+SIG

@pytest.mark.parametrize('path',['../artifacts-private','/etc','../outside'])
def test_browse_rejects_escape(artifacts,path):
    app=server.create_app(start_workers=False)
    assert testing.TestClient(app).simulate_get('/browse',params={'path':path}).status_code == 403

def test_browse_escapes_names_and_handles_relative_root(artifacts):
    (artifacts/'extensions'/'<script>.txt').write_text('fixture')
    response=testing.TestClient(server.create_app(False)).simulate_get('/browse',params={'path':'extensions'})
    assert response.status_code == 200
    assert '<script>' not in response.text
    assert '&lt;script&gt;.txt' in response.text

def test_browse_rejects_symlink_escape(artifacts,tmp_path):
    (artifacts/'extensions/escape').symlink_to('/etc',target_is_directory=True)
    assert testing.TestClient(server.create_app(False)).simulate_get('/browse',params={'path':'extensions/escape'}).status_code == 403

def zipbytes(name='extension/package.json'):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:archive.writestr(name,'fixture only')
    return output.getvalue()

def downloadable():
    ext=sync.VSCExtensionDefinition('sample.tool')
    ext.versions=[{'version':'1.0.0','files':[{'source':'https://upstream.invalid/vsix','assetType':VSIX},{'source':'https://upstream.invalid/signature','assetType':SIG}]}]
    return ext

def response(content):
    r=requests.Response();r.status_code=200;r._content=content
    return r

def test_http_error_not_written(artifacts):
    ext=downloadable();bad=response(b'Access Denied');bad.status_code=403
    session=Mock();session.get.return_value=bad
    with pytest.raises(requests.HTTPError):ext.download_assets(artifacts/'extensions',session)
    assert not list((artifacts/'extensions').rglob(VSIX))
    assert not list((artifacts/'extensions').rglob('latest.json'))

def test_unchanged_signature_bytes_and_receipt_cache(artifacts):
    ext=downloadable();vsix=zipbytes();signature=zipbytes('.signature.p7s')
    session=Mock();session.get.side_effect=[response(vsix),response(signature)]
    ext.download_assets(artifacts/'extensions',session);ext.save_state(artifacts/'extensions')
    folder=artifacts/'extensions/sample.tool/1.0.0'
    assert (folder/VSIX).read_bytes() == vsix
    assert (folder/SIG).read_bytes() == signature
    ext.download_assets(artifacts/'extensions',session)
    assert session.get.call_count == 2
    (folder/VSIX).write_bytes(b'corrupted fixture')
    session.get.side_effect=[response(vsix)]
    ext.download_assets(artifacts/'extensions',session)
    assert session.get.call_count == 3

@pytest.mark.parametrize('content',[b'error page',b''])
def test_bad_zip_rejected(artifacts,content):
    session=Mock();session.get.return_value=response(content)
    with pytest.raises(ValueError):downloadable().download_assets(artifacts/'extensions',session)

def test_unreceipted_old_cache_redownloaded(artifacts):
    folder=artifacts/'extensions/sample.tool/1.0.0';folder.mkdir(parents=True);(folder/VSIX).write_bytes(b'old cached 403')
    session=Mock();session.get.side_effect=[response(zipbytes()),response(zipbytes('.signature.p7s'))]
    downloadable().download_assets(artifacts/'extensions',session)
    assert session.get.call_count == 2

def test_dependencies_and_packs(artifacts):
    ext=downloadable();folder=artifacts/'extensions/sample.tool/1.0.0';folder.mkdir(parents=True)
    (folder/'Microsoft.VisualStudio.Code.Manifest').write_text(json.dumps({'extensionPack':['sample.pack'],'extensionDependencies':['sample.dep','sample.pack']}))
    mp=Mock();mp.search_by_extension_name.side_effect=lambda name: SimpleNamespace(identity=name)
    assert [e.identity for e in ext.process_embedded_extensions(artifacts/'extensions',mp)] == ['sample.pack','sample.dep']

def test_latest_route_statistics_and_catalog(artifacts):
    folder=artifacts/'extensions/sample.tool';folder.mkdir();(folder/'latest.json').write_text(json.dumps(extension()))
    c=testing.TestClient(server.create_app(False))
    assert c.simulate_get('/_apis/public/gallery/vscode/sample/tool/latest').json['identity'] == 'sample.tool'
    assert c.simulate_get('/_apis/public/gallery/vscode/missing/tool/latest').status_code == 404
    assert c.simulate_post('/_apis/public/gallery/publishers/sample/extensions/tool/1.0.0/stats').status_code == 204
    assert len(c.simulate_get('/api/catalog').json['extensions']) == 1

def test_signature_asset_served_exactly(artifacts):
    folder=artifacts/'extensions/sample.tool/1.0.0';folder.mkdir(parents=True)
    signature=zipbytes('.signature.p7s');(folder/SIG).write_bytes(signature)
    c=testing.TestClient(server.create_app(False))
    assert c.simulate_get('/artifacts/extensions/sample.tool/1.0.0/'+SIG).content == signature

def test_corrupt_manifest_does_not_hide_valid_extensions(gallery,artifacts):
    for name,data in [('sample.valid',extension('sample.valid')),('sample.invalid',{'identity':'sample.invalid','versions':[]})]:
        folder=artifacts/'extensions'/name;folder.mkdir();(folder/'latest.json').write_text(json.dumps(data))
    gallery.update_state()
    assert list(gallery.extensions) == ['sample.valid']

def test_all_historic_platform_variants_are_loaded(gallery,artifacts):
    folder=artifacts/'extensions/sample.tool';folder.mkdir()
    (folder/'latest.json').write_text(json.dumps(extension()))
    old=extension(platform='linux-x64');old['versions'][0]['version']='0.9.0'
    windows=extension(platform='win32-x64')['versions'][0];windows['version']='0.9.0'
    old['versions'].append(windows)
    historic=folder/'0.9.0';historic.mkdir();(historic/'extension.json').write_text(json.dumps(old))
    gallery.update_state()
    assert {(v['version'],v.get('targetPlatform')) for v in gallery.extensions['sample.tool']['versions']} == {('1.0.0',None),('0.9.0','linux-x64'),('0.9.0','win32-x64')}

def test_query_sort_reaches_upstream_request():
    session=Mock();reply=response(b'');reply._content=json.dumps({'results':[{'extensions':[], 'resultMetadata':[{'metadataType':'ResultCount','metadataItems':[{'count':0}]}]}]}).encode();session.post.return_value=reply
    mp=sync.VSCMarketplace(False,False,'1.144.0',session);mp.search_top_n(1)
    f=session.post.call_args.kwargs['json']['filters'][0]
    assert f['sortBy']==int(vsc.SortBy.InstallCount)
    assert f['sortOrder']==int(vsc.SortOrder.Descending)

def test_static_assets_reject_symlink_escape_and_internal_receipts(artifacts,tmp_path):
    outside=tmp_path.parent/'synthetic-private.txt';outside.write_text('fixture only')
    (artifacts/'escape').symlink_to(outside)
    (artifacts/'.private.sha256').write_text('fixture only')
    c=testing.TestClient(server.create_app(False))
    assert c.simulate_get('/artifacts/escape').status_code == 403
    assert c.simulate_get('/artifacts/.private.sha256').status_code == 403

def test_untrusted_html_asset_sandboxed(artifacts):
    (artifacts/'sample.html').write_text('<script>alert(1)</script>')
    c=testing.TestClient(server.create_app(False));r=c.simulate_get('/artifacts/sample.html')
    assert r.status_code==200
    assert "sandbox" in r.headers['content-security-policy']
    assert r.headers['x-content-type-options']=='nosniff'

def test_transitive_dependencies_cycle_safe_and_blocked():
    a,b,c,blocked=[Mock(identity=n) for n in ['sample.a','sample.b','sample.c','sample.blocked']]
    a.process_embedded_extensions.return_value=[b,blocked]
    b.process_embedded_extensions.return_value=[c]
    c.process_embedded_extensions.return_value=[a]
    sync.mirror_extensions([a],'unused',Mock(),Mock(),blocked=['SAMPLE.BLOCKED'])
    for ext in [a,b,c]:
        ext.download_assets.assert_called_once()
        ext.save_state.assert_called_once()
    blocked.download_assets.assert_not_called()

def test_failed_download_never_publishes_metadata():
    a=Mock(identity='sample.a');a.download_assets.side_effect=ValueError('HTTP error')
    with pytest.raises(ValueError):sync.mirror_extensions([a],'unused',Mock(),Mock())
    a.save_state.assert_not_called()

@pytest.mark.parametrize('field,sortby', [('displayName',vsc.SortBy.Title),('publisher',vsc.SortBy.PublisherName)])
def test_sort_direction(gallery,field,sortby):
    a,b=extension('a.first'),extension('b.second')
    values=[b,a];gallery._sort(values,sortby,vsc.SortOrder.Ascending)
    assert values==[a,b]
    gallery._sort(values,sortby,vsc.SortOrder.Descending)
    assert values==[b,a]

def test_import_has_no_server_or_observer_side_effects():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c','import sys;sys.path.insert(0,"vscoffline");import server;print("import ok")'],capture_output=True,text=True,timeout=5)
    assert result.returncode==0
    assert result.stdout.strip()=='import ok'

def test_429_retries_are_bounded(monkeypatch):
    session=Mock();reply=response(b'');reply.status_code=429;session.post.return_value=reply
    mp=sync.VSCMarketplace(False,False,'1.144.0',session)
    monkeypatch.setattr(sync.time,'sleep',lambda seconds:None)
    assert mp.search_top_n(1)==[]
    assert session.post.call_count==10


def test_bad_signature_archive_does_not_publish_state(artifacts):
    session=Mock();session.get.side_effect=[response(zipbytes()),response(zipbytes('wrong-entry'))]
    with pytest.raises(ValueError):downloadable().download_assets(artifacts/'extensions',session)
    assert not list((artifacts/'extensions').rglob('latest.json'))

def test_binaries_catalog_and_detail_routes(artifacts):
    folder=artifacts/'installers/linux-x64/stable';folder.mkdir(parents=True)
    binary={'name':'1.144.0','version':'fixture-commit','platform':'linux','architecture':'x64','quality':'stable','sha256hash':'fixture-checksum'}
    (folder/'latest.json').write_text(json.dumps(binary));(folder/'fixture-commit.json').write_text(json.dumps(binary));(folder/'vscode-1.144.0.tar.gz').write_bytes(b'fixture only')
    c=testing.TestClient(server.create_app(False));entry=c.simulate_get('/api/catalog').json['binaries'][0]
    assert entry['identity']=='linux-x64'
    assert len(entry['versions'])==1
    assert entry['versions'][0]['sizeBytes']==12
    assert entry['versions'][0]['downloadUrl'].endswith('/artifacts/installers/linux-x64/stable/vscode-1.144.0.tar.gz')
    assert c.simulate_get('/binaries').status_code==200
    assert c.simulate_get('/binaries/linux-x64/stable').status_code==200


def test_ui_assets_and_extension_detail_route(artifacts):
    c=testing.TestClient(server.create_app(False))
    assert c.simulate_get('/extensions/sample.tool').status_code==200
    assert c.simulate_get('/ui/app.js').status_code==200
    assert c.simulate_get('/ui/style.css').content_type=='text/css'
    assert c.simulate_get('/ui/secret.txt').status_code==404

def test_vscode_marketplace_item_link_to_exact_local_extension(artifacts):
    folder=artifacts/'extensions/sample.tool';folder.mkdir();(folder/'latest.json').write_text(json.dumps(extension()))
    c=testing.TestClient(server.create_app(False))
    for params in [{'itemName':'sample.tool'},{'itemName':'SAMPLE.TOOL','ssr':'false'}]:
        response=c.simulate_get('/items',params=params)
        assert response.status_code==302
        assert response.headers['location']=='/extensions/sample.tool'
    assert c.simulate_get('/items',params={'itemName':'missing.tool'}).status_code==404
    assert c.simulate_get('/items',params={'itemName':'https://external.invalid'}).status_code==400
    assert c.simulate_get('/items').status_code==400
