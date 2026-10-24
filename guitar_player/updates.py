"""Read the public stable release; checking never downloads or runs a file."""
from dataclasses import dataclass
import json
import re
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

REPOSITORY='https://github.com/DIEtype/Guitarplayer-For-NTE'
RELEASES_URL=REPOSITORY+'/releases'
API_URL='https://api.github.com/repos/DIEtype/Guitarplayer-For-NTE/releases/latest'
TUTORIAL_URL='https://www.bilibili.com/video/BV1UMnL6yEkV/'
MIRROR_URL='https://pan.baidu.com/s/1Zk7ZYHZxvSpX1IQWtjLZGw?pwd=9w2e'
MIRROR_CODE='9w2e'
CREDIT='by Akari'
MAX_RESPONSE=1_000_000


class UpdateCheckError(ValueError):pass


def version_parts(value):
    if not isinstance(value,str) or not re.fullmatch(r'v?\d{1,6}\.\d{1,6}\.\d{1,6}',value):
        raise UpdateCheckError('版本号格式无法识别；请打开 GitHub 下载页确认最新版本。')
    return tuple(int(n) for n in value.removeprefix('v').split('.'))


@dataclass(frozen=True)
class ReleaseInfo:
    version:str
    current:str
    url:str

    @property
    def newer(self):return version_parts(self.version)>version_parts(self.current)

    @property
    def message(self):
        if self.newer:return f'发现新版 v{self.version}，当前 v{self.current}；点击“查看新版”前往下载。'
        if version_parts(self.version)<version_parts(self.current):
            return f'当前 v{self.current} 高于 GitHub 正式版 v{self.version}，无需更新。'
        return f'当前 v{self.current} 与 GitHub 最新正式版一致。'


def parse_release(data,current):
    version_parts(current)
    if not isinstance(data,dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        raise UpdateCheckError('未获取到公开正式版本；请打开 GitHub 下载页查看。')
    tag=data.get('tag_name');version_parts(tag)
    # Construct the link from a validated tag, rather than opening a URL supplied
    # by an API response. Only the app's own public repository is used.
    return ReleaseInfo(tag.removeprefix('v'),current.removeprefix('v'),RELEASES_URL+'/tag/'+tag)


def check_latest(current,*,opener=None,timeout=5):
    version_parts(current)
    request=Request(API_URL,headers={'Accept':'application/vnd.github+json',
        'User-Agent':'NTE-Guitar-Player/'+current,'X-GitHub-Api-Version':'2026-03-10'})
    try:
        with (opener or urlopen)(request,timeout=timeout) as response:
            raw=response.read(MAX_RESPONSE+1)
        if len(raw)>MAX_RESPONSE:raise UpdateCheckError('更新信息过大，未读取；请打开 GitHub 下载页查看。')
        data=json.loads(raw.decode('utf-8'))
    except HTTPError as error:
        if error.code==404:
            raise UpdateCheckError('GitHub 尚无可读取的正式版；请打开 GitHub 下载页确认。') from error
        if error.code in (403,429):
            raise UpdateCheckError('GitHub 暂时拒绝请求或已限流；请稍后重试，或打开下载页。') from error
        raise UpdateCheckError(f'GitHub 返回 HTTP {error.code}；请稍后重试，或使用百度网盘入口。') from error
    except (URLError,OSError,TimeoutError) as error:
        raise UpdateCheckError('暂时无法连接 GitHub；请检查网络后重试，也可打开百度网盘。') from error
    except (UnicodeError,json.JSONDecodeError) as error:
        raise UpdateCheckError('GitHub 更新信息无法读取；请稍后重试，或打开下载页。') from error
    return parse_release(data,current)
