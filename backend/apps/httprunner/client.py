import json
import time

import requests
import urllib3
from loguru import logger
from requests import Request, Response
from requests.exceptions import (
    InvalidSchema,
    InvalidURL,
    MissingSchema,
    RequestException,
)

from httprunner.models import RequestData, ResponseData
from httprunner.models import SessionData, ReqRespData
from httprunner.utils import lower_dict_keys, omit_long_data

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ApiResponse(Response):
    def raise_for_status(self):
        if hasattr(self, "error") and self.error:
            raise self.error
        Response.raise_for_status(self)


def get_req_resp_record(resp_obj: Response) -> ReqRespData:
    """ get request and response info from Response() object.
    """

    def log_print(req_or_resp, r_type):
        msg = f"\n================== {r_type} details ==================\n"
        for key, value in req_or_resp.model_dump().items():
            # 特殊处理method字段，确保显示枚举值而不是枚举对象
            if key == "method" and hasattr(value, 'value'):
                value = value.value
            elif isinstance(value, dict) or isinstance(value, list):
                value = json.dumps(value, indent=4, ensure_ascii=False)

            msg += "{:<8} : {}\n".format(key, value)
        logger.debug(msg)

    # record actual request info
    request_headers = dict(resp_obj.request.headers)
    request_cookies = resp_obj.request._cookies.get_dict()

    request_body = resp_obj.request.body
    if request_body is not None:
        try:
            request_body = json.loads(request_body)
        except json.JSONDecodeError:
            # str: a=1&b=2
            pass
        except UnicodeDecodeError:
            # bytes/bytearray: request body in protobuf
            pass
        except TypeError:
            # neither str nor bytes/bytearray, e.g. <MultipartEncoder>
            pass

        request_content_type = lower_dict_keys(request_headers).get("content-type")
        if request_content_type and "multipart/form-data" in request_content_type:
            # upload file type
            request_body = "upload file stream (OMITTED)"

    request_data = RequestData(
        method=resp_obj.request.method,
        url=resp_obj.request.url,
        headers=request_headers,
        cookies=request_cookies,
        body=request_body,
    )

    # log request details in debug mode
    log_print(request_data, "request")

    # record response info
    resp_headers = dict(resp_obj.headers)
    lower_resp_headers = lower_dict_keys(resp_headers)
    content_type = lower_resp_headers.get("content-type", "")

    if "image" in content_type:
        # response is image type, record bytes content only
        response_body = resp_obj.content
    else:
        try:
            # try to record json data
            response_body = resp_obj.json()
        except ValueError:
            # only record at most 512 text charactors
            resp_text = resp_obj.text
            response_body = omit_long_data(resp_text)

    response_data = ResponseData(
        status_code=resp_obj.status_code,
        cookies=resp_obj.cookies or {},
        encoding=resp_obj.encoding,
        headers=resp_headers,
        content_type=content_type,
        body=response_body,
    )

    # log response details in debug mode
    log_print(response_data, "response")

    req_resp_data = ReqRespData(request=request_data, response=response_data)
    return req_resp_data


class HttpSession(requests.Session):
    """
    Class for performing HTTP requests and holding (session-) cookies between requests (in order
    to be able to log in and out of websites). Each request is logged so that HttpRunner can
    display statistics.

    This is a slightly extended version of `python-request <http://python-requests.org>`_'s
    :py:class:`requests.Session` class and mostly this class works exactly the same.
    """

    def __init__(self):
        super(HttpSession, self).__init__()
        self.data = SessionData()

    def update_last_req_resp_record(self, resp_obj):
        """
        update request and response info from Response() object.
        """
        # TODO: fix
        self.data.req_resps.pop()
        self.data.req_resps.append(get_req_resp_record(resp_obj))

    def request(self, method, url, name=None, **kwargs):
        """
        Constructs and sends a :py:class:`requests.Request`.
        Returns :py:class:`requests.Response` object.

        :param method:
            method for the new :class:`Request` object.
        :param url:
            URL for the new :class:`Request` object.
        :param name: (optional)
            Placeholder, make compatible with Locust's HttpSession
        :param params: (optional)
            Dictionary or bytes to be sent in the query string for the :class:`Request`.
        :param data: (optional)
            Dictionary or bytes to send in the body of the :class:`Request`.
        :param headers: (optional)
            Dictionary of HTTP Headers to send with the :class:`Request`.
        :param cookies: (optional)
            Dict or CookieJar object to send with the :class:`Request`.
        :param files: (optional)
            Dictionary of ``'filename': file-like-objects`` for multipart encoding upload.
        :param auth: (optional)
            Auth tuple or callable to enable Basic/Digest/Custom HTTP Auth.
        :param timeout: (optional)
            How long to wait for the server to send data before giving up, as a float, or \
            a (`connect timeout, read timeout <user/advanced.html#timeouts>`_) tuple.
            :type timeout: float or tuple
        :param allow_redirects: (optional)
            Set to True by default.
        :type allow_redirects: bool
        :param proxies: (optional)
            Dictionary mapping protocol to the URL of the proxy.
        :param stream: (optional)
            whether to immediately download the response content. Defaults to ``False``.
        :param verify: (optional)
            if ``True``, the SSL cert will be verified. A CA_BUNDLE path can also be provided.
        :param cert: (optional)
            if String, path to ssl client cert file (.pem). If Tuple, ('cert', 'key') pair.
        """
        self.data = SessionData()

        # timeout default to 120 seconds
        kwargs.setdefault("timeout", 120)

        # set stream to True, in order to get client/server IP/Port
        kwargs["stream"] = True

        start_timestamp = time.time()
        response = self._send_request_safe_mode(method, url, **kwargs)
        response_time_ms = round((time.time() - start_timestamp) * 1000, 2)

        try:
            client_ip, client_port = response.raw._connection.sock.getsockname()
            self.data.address.client_ip = client_ip
            self.data.address.client_port = client_port
            logger.debug(f"client IP: {client_ip}, Port: {client_port}")
        except Exception:
            pass

        try:
            server_ip, server_port = response.raw._connection.sock.getpeername()
            self.data.address.server_ip = server_ip
            self.data.address.server_port = server_port
            logger.debug(f"server IP: {server_ip}, Port: {server_port}")
        except Exception:
            pass

        # get length of the response content
        content_size = int(dict(response.headers).get("content-length") or 0)

        # record the consumed time
        self.data.stat.response_time_ms = response_time_ms
        self.data.stat.elapsed_ms = response.elapsed.microseconds / 1000.0
        self.data.stat.content_size = content_size

        # record request and response histories, include 30X redirection
        response_list = response.history + [response]
        try:
            self.data.req_resps = [
                get_req_resp_record(resp_obj) for resp_obj in response_list
            ]
        except Exception as e:
            logger.error(f"Failed to record request/response data: {e}")
            # 如果记录失败，至少保存一个基本的记录
            try:
                basic_req_resp = get_req_resp_record(response)
                self.data.req_resps = [basic_req_resp]
            except Exception as basic_e:
                logger.error(f"Failed to create basic request/response record: {basic_e}")
                # 创建最基础的记录
                from httprunner.models import RequestData, ResponseData, ReqRespData
                request_data = RequestData(
                    method=method,
                    url=url,
                    headers={},
                    cookies={},
                    body=None,
                )
                response_data = ResponseData(
                    status_code=getattr(response, 'status_code', 0),
                    headers={},
                    cookies={},
                    encoding=None,
                    content_type="",
                    body="Failed to parse response",
                )
                self.data.req_resps = [ReqRespData(request=request_data, response=response_data)]

        try:
            response.raise_for_status()
        except RequestException as ex:
            logger.error(f"{str(ex)}")
            logger.error(f"response::{response}")
        else:
            logger.info(
                f"status_code: {response.status_code}, "
                f"response_time(ms): {response_time_ms} ms, "
                f"response_length: {content_size} bytes"
            )

        return response

    def _send_request_safe_mode(self, method, url, **kwargs):
        """
        Send a HTTP request, and catch any exception that might occur due to connection problems.
        Safe mode has been removed from requests 1.x.
        """
        try:
            return requests.Session.request(self, method, url, **kwargs)
        except (MissingSchema, InvalidSchema, InvalidURL):
            raise
        except RequestException as ex:
            resp = ApiResponse()
            resp.error = ex
            resp.status_code = 0  # with this status_code, content returns None
            resp.request = Request(method, url).prepare()
            
            # 根据异常类型设置适当的状态码
            if "NameResolutionError" in str(type(ex)) or "Failed to resolve" in str(ex):
                resp.status_code = 400  # Bad Request for DNS resolution failure
            elif "ConnectionError" in str(type(ex)):
                resp.status_code = 503  # Service Unavailable for connection errors
            elif "Timeout" in str(type(ex)):
                resp.status_code = 408  # Request Timeout
            elif "SSL" in str(type(ex)) or "certificate" in str(ex).lower():
                resp.status_code = 495  # SSL Certificate Error
            else:
                resp.status_code = 500  # Internal Server Error for other network issues
            
            # 即使发生异常，也要保存基本的请求和响应数据
            try:
                # 记录请求信息
                request_headers = dict(resp.request.headers) if resp.request else {}
                request_cookies = resp.request._cookies.get_dict() if resp.request and hasattr(resp.request, '_cookies') else {}
                
                request_data = RequestData(
                    method=method,
                    url=url,
                    headers=request_headers,
                    cookies=request_cookies,
                    body=kwargs.get('json') or kwargs.get('data'),
                )
                
                # 记录响应信息（即使失败）
                response_data = ResponseData(
                    status_code=resp.status_code,  # 使用设置的状态码
                    headers={},
                    cookies={},
                    encoding=None,
                    content_type="",
                    body=f"Request failed: {str(ex)}",
                )
                
                # 保存到session data中
                req_resp_data = ReqRespData(request=request_data, response=response_data)
                self.data.req_resps = [req_resp_data]
                
                # 记录统计信息
                self.data.stat.response_time_ms = 0
                self.data.stat.elapsed_ms = 0
                self.data.stat.content_size = 0
                
            except Exception as data_ex:
                logger.error(f"Failed to save request/response data for failed request: {data_ex}")
            
            return resp
