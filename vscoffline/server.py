import os, time, glob
import mimetypes
import falcon
from packaging.version import Version, InvalidVersion
from pathlib import Path
from urllib.parse import quote, urlencode
from html import escape
from threading import Lock


class SecurityHeaders:
    def process_response(self, req, resp, resource, req_succeeded):
        resp.set_header("X-Content-Type-Options", "nosniff")
        resp.set_header("Referrer-Policy", "no-referrer")
        resp.set_header("X-Frame-Options", "DENY")
import logging as log
from wsgiref import simple_server
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler
from threading import Event, Thread
import vsc


class VSCUpdater(object):

    def on_get(self, req, resp, platform, buildquality, commitid):
        try:
            updatedir = str(vsc.safe_path(vsc.ARTIFACTS_INSTALLERS, platform, buildquality))
        except ValueError:
            raise falcon.HTTPBadRequest(description='Invalid platform or quality')
        if not os.path.exists(updatedir):
            log.warning(f'Update build directory does not exist at {updatedir}. Check sync or sync configuration.')
            resp.status = falcon.HTTP_500
            return
        latestpath = os.path.join(updatedir, 'latest.json')
        latest = vsc.Utility.load_json(latestpath)
        if not latest:
            resp.content = 'Unable to load latest.json'
            log.warning(f'Unable to load latest.json for platform {platform} and buildquality {buildquality}')
            resp.status = falcon.HTTP_500
            return
        if latest['version'] == commitid:
            # No update available
            log.debug(f'Client {platform}, Quality {buildquality}. No Update available.')
            resp.status = falcon.HTTP_204
            return
        name = latest['name']
        updatepath = vsc.Utility.first_file(updatedir, f'vscode-{name}.*')
        if not updatepath:
            resp.content = 'Unable to find update payload'
            log.warning(f'Unable to find update payload from {updatedir}/vscode-{name}.*')
            resp.status = falcon.HTTP_404
            return
        if not vsc.Utility.hash_file_and_check(updatepath, latest['sha256hash']):
            resp.content = 'Update payload hash mismatch'
            log.warning(f'Update payload hash mismatch {updatepath}')
            resp.status = falcon.HTTP_403
            return
        # Url to get update
        latest['url'] = vsc.artifact_url(updatepath)
        log.debug(f'Client {platform}, Quality {buildquality}. Providing update {updatepath}')
        resp.status = falcon.HTTP_200
        resp.media = latest

class VSCBinaryFromCommitId(object):

    def on_get(self, req, resp, commitid, platform, buildquality):
        try:
            updatedir = str(vsc.safe_path(vsc.ARTIFACTS_INSTALLERS, platform, buildquality))
        except ValueError:
            raise falcon.HTTPBadRequest(description='Invalid platform or quality')
        if not os.path.exists(updatedir):
            log.warning(f'Update build directory does not exist at {updatedir}. Check sync or sync configuration.')
            resp.status = falcon.HTTP_500
            return
        try:
            jsonpath = str(vsc.safe_path(updatedir, f'{commitid}.json'))
        except ValueError:
            raise falcon.HTTPBadRequest(description='Invalid commit identifier')
        updatejson = vsc.Utility.load_json(jsonpath)
        if not updatejson:
            resp.content = f'Unable to load {jsonpath}'
            log.warning(resp.content)
            resp.status = falcon.HTTP_500
            return
        name = updatejson['name']
        updatepath = vsc.Utility.first_file(updatedir, f'vscode-{name}.*')
        if not updatepath:
            resp.content = f'Unable to find update payload from {updatedir}/vscode-{name}.*'
            log.warning(resp.content)
            resp.status = falcon.HTTP_404
            return
        if not vsc.Utility.hash_file_and_check(updatepath, updatejson['sha256hash']):
            resp.content = f'Update payload hash mismatch {updatepath}'
            log.warning(resp.content)
            resp.status = falcon.HTTP_403
            return
        # Url for the client to fetch the update
        resp.set_header('Location', vsc.artifact_url(updatepath))
        resp.status = falcon.HTTP_302

class VSCRecommendations(object):

    def on_get(self, req, resp):
        if not os.path.exists(vsc.ARTIFACT_RECOMMENDATION):
            resp.status = falcon.HTTP_404
            return
        resp.status = falcon.HTTP_200
        resp.content_type = 'application/octet-stream'
        with open(vsc.ARTIFACT_RECOMMENDATION, 'r') as f:
            resp.text = f.read()

class VSCMalicious(object):

    def on_get(self, req, resp):
        if not os.path.exists(vsc.ARTIFACT_MALICIOUS):
            resp.status = falcon.HTTP_404
            return
        resp.status = falcon.HTTP_200
        resp.content_type = 'application/octet-stream'
        with open(vsc.ARTIFACT_MALICIOUS, 'r') as f:
            resp.text = f.read()

class VSCGallery(object):

    def __init__(self, interval=3600, start_worker=True):
        self._refresh_lock = Lock()
        self.extensions = {}
        self.interval = interval
        self.loaded = Event()
        self.update_worker = Thread(target=self.update_state_loop, args=())
        self.update_worker.daemon = True
        self.update_state()
        self.loaded.set()
        if start_worker:
            self.update_worker.start()

    def update_state(self):
        with self._refresh_lock:
            self._load_state()

    def _load_state(self):
        snapshot = {}
        # Load each extension
        for extensiondir in glob.glob(vsc.ARTIFACTS_EXTENSIONS + '/*/'):

            # Load the latest version of each extension
            latestpath = os.path.join(extensiondir, 'latest.json')
            latest = vsc.Utility.load_json(latestpath)

            if not latest:
                log.debug(f'Tried to load invalid manifest json {latestpath}')
                continue

            try:
                latest = self.process_loaded_extension(latest, extensiondir)
            except (KeyError, TypeError, ValueError):
                log.warning('Skipping invalid extension metadata: %s', latestpath)
                continue

            if not latest:
                log.debug(f'Unable to determine latest version {latestpath}')
                continue


            # Find other versions
            for versionpath in glob.glob(extensiondir + '/*/extension.json') + glob.glob(extensiondir + '/*/*/extension.json'):
                #log.info(f'Version path: {versionpath}')
                vers = vsc.Utility.load_json(versionpath)
                if not vers:
                    log.debug(f'Tried to load invalid version manifest json {versionpath}')
                    continue
                try:
                    vers = self.process_loaded_extension(vers, extensiondir)
                except (KeyError, TypeError, ValueError):
                    log.warning('Skipping invalid version metadata: %s', versionpath)
                    continue

                # If this extension.json is actually the latest version, then ignore it
                if not vers:
                    continue
                # Keep every version/platform pair, not only the first entry.
                for candidate in vers['versions']:
                    if not any(v['version'] == candidate['version'] and v.get('targetPlatform') == candidate.get('targetPlatform') for v in latest['versions']):
                        latest['versions'].append(candidate)

            # Sort versions
            try:
                latest['versions'] = sorted(latest['versions'], key=lambda k: Version(k['version']), reverse=True)
            except InvalidVersion:
                log.warning('Skipping invalid extension version: %s', latestpath)
                continue

            # Save the extension in the cache
            name = latest['identity']
            snapshot[name] = latest

        self.extensions = snapshot

        log.info(f'Loaded {len(self.extensions)} extensions')

    def process_loaded_extension(self, extension, extensiondir):
            name = extension['identity']
            if not extension.get('versions') or not isinstance(name, str):
                raise ValueError('Extension needs identity and versions')

            # Repoint asset urls
            for version in extension["versions"]:
                if "targetPlatform" in version:
                    targetPlatform = version['targetPlatform']
                    asseturi = vsc.artifact_url(vsc.safe_path(extensiondir, version['version'], targetPlatform))
                else:
                    asseturi = vsc.artifact_url(vsc.safe_path(extensiondir, version['version']))

                version['assetUri'] = asseturi
                version['fallbackAssetUri'] = asseturi
                for asset in version['files']:
                    asset['source'] = asseturi + '/' + asset['assetType']

            # Map statistics for later lookup
            stats = {
                'averagerating': 0,
                'install': 0,
                'weightedRating': 0
            }
            if 'statistics' not in extension or not extension['statistics']:
                log.info(f'Statistics are missing from extension {name} in {extensiondir}, generating.')
            else:
                extension_statistics = {}
                for statistic in extension['statistics']:
                    extension_statistics[statistic['statisticName']] = statistic['value']
                stats.update(extension_statistics)
            extension['stats'] = stats
            return extension

    def update_state_loop(self):
        while True:
            self.update_state()
            self.loaded.set()
            log.info(f'Checking for updates in {vsc.Utility.seconds_to_human_time(self.interval)}')
            time.sleep(self.interval)

    def on_post(self, req, resp):
        body = req.media
        if not isinstance(body, dict) or not isinstance(body.get("filters"), list) or not body["filters"]:
            raise falcon.HTTPBadRequest(description="Expected a non-empty filters array")
        query = body["filters"][0]
        if not isinstance(query, dict) or not isinstance(query.get("criteria"), list):
            raise falcon.HTTPBadRequest(description="Expected filter criteria")
        try:
            sortby = vsc.SortBy(query.get("sortBy", 0))
            sortorder = vsc.SortOrder(query.get("sortOrder", 0))
            page = int(query.get("pageNumber", 1))
            size = int(query.get("pageSize", 500))
            if page < 1 or not 1 <= size <= 1000:
                raise ValueError()
            if any(not isinstance(c, dict) or not isinstance(c.get("filterType"), int) or not isinstance(c.get("value"), str) for c in query["criteria"]):
                raise ValueError()
        except (ValueError, TypeError):
            raise falcon.HTTPBadRequest(description="Invalid sort, paging or criteria")
        if sortby == vsc.SortBy.NoneOrRelevance:
            sortby, sortorder = vsc.SortBy.InstallCount, vsc.SortOrder.Descending
        result = self._apply_criteria(query["criteria"])
        self._sort(result, sortby, sortorder)
        total = len(result)
        resp.media = self._build_response(result[(page - 1) * size:page * size], total)
        resp.status = falcon.HTTP_200

    def _sort(self, result, sortby, sortorder):
        if sortorder == vsc.SortOrder.Ascending:
            rev = False
        else:
            rev = True

        if sortby == vsc.SortBy.PublisherName:
            result.sort(key=lambda k: k['publisher']['publisherName'], reverse=rev)

        elif sortby == vsc.SortBy.InstallCount:
            result.sort(key=lambda k: k['stats']['install'], reverse=rev)

        elif sortby == vsc.SortBy.AverageRating:
            result.sort(key=lambda k: k['stats']['averagerating'], reverse=rev)

        elif sortby == vsc.SortBy.WeightedRating:
            result.sort(key=lambda k: k['stats']['weightedRating'], reverse=rev)

        elif sortby == vsc.SortBy.LastUpdatedDate:
            result.sort(key=lambda k: vsc.Utility.from_json_datetime(k['lastUpdated']), reverse=rev)

        elif sortby == vsc.SortBy.PublishedDate:
            result.sort(key=lambda k: vsc.Utility.from_json_datetime(k['publishedDate']), reverse=rev)

        else:
            result.sort(key=lambda k: k.get('displayName', k['identity']).lower(), reverse=rev)

    def _apply_criteria(self, criteria):
        extensions = list(self.extensions.values())
        names = [c["value"].lower() for c in criteria if c.get("filterType") == vsc.FilterType.ExtensionName]
        ids = [c["value"].lower() for c in criteria if c.get("filterType") == vsc.FilterType.ExtensionId]
        # Multiple requested names/IDs are alternatives. Other filter kinds intersect.
        if names or ids:
            extensions = [e for e in extensions if e["identity"].lower() in names or e.get("extensionId", "").lower() in ids]
        for c in criteria:
            value = c.get("value", "").lower()
            kind = c.get("filterType")
            if kind == vsc.FilterType.SearchText and value:
                extensions = [e for e in extensions if any(value in str(e.get(k, "")).lower() for k in ("identity", "displayName", "shortDescription"))]
            elif kind == vsc.FilterType.Tag:
                extensions = [e for e in extensions if value in [t.lower() for t in e.get("tags", [])]]
            elif kind == vsc.FilterType.Category:
                extensions = [e for e in extensions if value in [t.lower() for t in e.get("categories", [])]]
            elif kind == vsc.FilterType.ExcludeWithFlags:
                # The cached metadata uses textual flags (for example "validated, public").
                if value.isdigit() and int(value) & int(vsc.QueryFlags.Unpublished):
                    extensions = [e for e in extensions if "unpublished" not in str(e.get("flags", "")).lower()]
        return extensions

    def _build_response(self, resultingExtensions, total=None):
        result = {
            'results': [
                {
                    'extensions': resultingExtensions,
                    'pagingToken': None,
                    'resultMetadata': [
                        {
                            'metadataType': 'ResultCount',
                            'metadataItems': [
                                {
                                    'name': 'TotalCount',
                                    'count': len(resultingExtensions) if total is None else total
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        return result

class VSCIndex(object):

    def __init__(self):
        pass

    def on_get(self, req, resp):
        resp.content_type = 'text/html'
        with open(vsc.CONTENT / 'index.html', 'r') as f:
            resp.text = f.read()
        resp.status = falcon.HTTP_200

class VSCDirectoryBrowse(object):

    def __init__(self, root):
        self.root = root

    def on_get(self, req, resp):
        try:
            requested = vsc.safe_path(self.root, req.get_param("path") or "")
        except ValueError:
            raise falcon.HTTPForbidden(description="Path outside artifacts")
        if not requested.is_dir():
            raise falcon.HTTPNotFound()
        resp.content_type = "text/html"
        template = (vsc.CONTENT / "browse.html").read_text()
        relative = requested.relative_to(Path(self.root).resolve()).as_posix()
        resp.text = template.replace("{PATH}", escape(relative)).replace("{CONTENT}", self.simple_dir_browse_response(requested))

    def simple_dir_browse_response(self, path):
        root = Path(self.root).resolve()
        rows = []
        for item in sorted(Path(path).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # Hide integrity receipts/internal temp files and symlinks leaving the root.
            if item.name.startswith(".") or item.is_symlink():
                continue
            relative = item.relative_to(root).as_posix()
            href = "/browse?" + urlencode({"path": relative}) if item.is_dir() else "/artifacts/" + quote(relative, safe="/")
            rows.append('<a class="file-row" href="' + escape(href, quote=True) + '"><span>' + ("Folder" if item.is_dir() else "File") + '</span><strong>' + escape(item.name) + '</strong></a>')
        return "\n".join(rows) or '<p class="empty">This folder is empty.</p>'


class VSCLatestExtension:
    def __init__(self, gallery):
        self.gallery = gallery

    def on_get(self, req, resp, publisher, name):
        extension = self.gallery.extensions.get((publisher + "." + name).lower())
        if extension is None:
            extension = next((e for key, e in self.gallery.extensions.items() if key.lower() == (publisher + "." + name).lower()), None)
        if extension is None:
            raise falcon.HTTPNotFound()
        resp.media = extension


class VSCStatistics:
    def on_post(self, req, resp, publisher, name, version):
        # Offline installs must not report usage upstream. Accept the protocol call without storing it.
        resp.status = falcon.HTTP_204


class VSCCatalog:
    def __init__(self, gallery):
        self.gallery = gallery

    def on_get(self, req, resp):
        resp.media = {"extensions": list(self.gallery.extensions.values()), "signatureVerification": "Performed by VS Code, not this mirror"}


class VSCArtifact:
    def on_get(self, req, resp, asset_path):
        try:
            if any(part.startswith(".") for part in Path(asset_path).parts):
                raise ValueError("Internal file")
            path = vsc.safe_path(vsc.ARTIFACTS, asset_path)
        except ValueError:
            raise falcon.HTTPForbidden()
        if not path.is_file():
            raise falcon.HTTPNotFound()
        resp.content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        resp.set_stream(path.open("rb"), path.stat().st_size)
        resp.set_header("X-Content-Type-Options", "nosniff")
        # Untrusted extension HTML/SVG is downloadable, not executable in the gallery origin.
        resp.set_header("Content-Security-Policy", "sandbox; default-src 'none'")


class ArtifactChangedHandler(FileSystemEventHandler):

    def __init__(self, gallery):
        self.gallery = gallery

    def on_modified(self, event):
        if 'updated.json' in event.src_path:
            log.info('Detected updated.json change, updating extension gallery')
            self.gallery.update_state()


def create_app(start_workers=True):
    """Create a testable WSGI app. No observer or directories are created on import."""
    Path(vsc.ARTIFACTS_INSTALLERS).mkdir(parents=True, exist_ok=True)
    Path(vsc.ARTIFACTS_EXTENSIONS).mkdir(parents=True, exist_ok=True)
    gallery = VSCGallery(start_worker=start_workers)
    if start_workers:
        observer = PollingObserver()
        observer.schedule(ArtifactChangedHandler(gallery), vsc.ARTIFACTS, recursive=False)
        observer.start()
    app = falcon.App(cors_enable=True, middleware=[SecurityHeaders()])
    app.add_route('/api/update/{platform}/{buildquality}/{commitid}', VSCUpdater())
    app.add_route('/commit:{commitid}/{platform}/{buildquality}', VSCBinaryFromCommitId())
    app.add_route('/extensions/workspaceRecommendations.json.gz', VSCRecommendations())
    app.add_route('/extensions/marketplace.json', VSCMalicious())
    app.add_route('/_apis/public/gallery/extensionquery', gallery)
    app.add_route('/_apis/public/gallery/vscode/{publisher}/{name}/latest', VSCLatestExtension(gallery))
    app.add_route('/_apis/public/gallery/publishers/{publisher}/extensions/{name}/{version}/stats', VSCStatistics())
    app.add_route('/api/catalog', VSCCatalog(gallery))
    app.add_route('/browse', VSCDirectoryBrowse(vsc.ARTIFACTS))
    app.add_route('/', VSCIndex())
    app.add_route('/artifacts/{asset_path:path}', VSCArtifact())
    return app


if __name__ == '__main__':
    log.basicConfig(
        format='[%(levelname)1.1s %(asctime)s %(module)s:%(lineno)d] %(message)s',
        datefmt='%y%m%d %H:%M:%S',
        level=log.DEBUG
    )
    httpd = simple_server.make_server('0.0.0.0', 5000, create_app())
    httpd.serve_forever()
