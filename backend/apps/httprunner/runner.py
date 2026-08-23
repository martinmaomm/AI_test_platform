import copy
import os
import re
import time
import uuid
from datetime import datetime
from typing import List, Dict, Text

try:
    import allure

    USE_ALLURE = True
except ModuleNotFoundError:
    USE_ALLURE = False

from loguru import logger

from httprunner import utils, exceptions
from httprunner.client import HttpSession
from httprunner.exceptions import ValidationFailure, ParamsError
from httprunner.ext.uploader import prepare_upload_step
from httprunner.loader import load_project_meta, load_testcase_file
from httprunner.parser import build_url, parse_data, parse_variables_mapping
from httprunner.response import ResponseObject
from httprunner.testcase import Config, Step
from httprunner.utils import merge_variables
from httprunner.models import (
    TConfig,
    TStep,
    VariablesMapping,
    StepData,
    TestCaseSummary,
    TestCaseTime,
    TestCaseInOut,
    ProjectMeta,
    TestCase,
    Hooks,
)


class HttpRunner(object):
    config: Config
    teststeps: List[Step]

    success: bool = False  # indicate testcase execution result
    __config: TConfig
    __teststeps: List[TStep]
    __project_meta: ProjectMeta = None
    __case_id: Text = ""
    __export: List[Text] = []
    __step_datas: List[StepData] = []
    __session: HttpSession = None
    __session_variables: VariablesMapping = {}
    # time
    __start_at: float = 0
    __duration: float = 0
    # log
    __log_path: Text = ""

    def __init_tests__(self):
        self.__config = self.config.perform()
        self.__teststeps = []
        for step in self.teststeps:
            self.__teststeps.append(step.perform())

    @property
    def raw_testcase(self) -> TestCase:
        if not hasattr(self, "__config"):
            self.__init_tests__()

        return TestCase(config=self.__config, teststeps=self.__teststeps)

    def with_project_meta(self, project_meta: ProjectMeta) -> "HttpRunner":
        self.__project_meta = project_meta
        return self

    def with_session(self, session: HttpSession) -> "HttpRunner":
        self.__session = session
        return self

    def with_case_id(self, case_id: Text) -> "HttpRunner":
        self.__case_id = case_id
        return self

    def with_variables(self, variables: VariablesMapping) -> "HttpRunner":
        self.__session_variables = variables
        return self

    def with_export(self, export: List[Text]) -> "HttpRunner":
        self.__export = export
        return self

    def __call_hooks(
        self, hooks: Hooks, step_variables: VariablesMapping, hook_msg: Text,
    ):
        """ call hook actions.

        Args:
            hooks (list): each hook in hooks list maybe in two format.

                format1 (str): only call hook functions.
                    ${func()}
                format2 (dict): assignment, the value returned by hook function will be assigned to variable.
                    {"var": "${func()}"}

            step_variables: current step variables to call hook, include two special variables

                request: parsed request dict
                response: ResponseObject for current response

            hook_msg: setup/teardown request/testcase

        """
        logger.info(f"call hook actions: {hook_msg}")

        if not isinstance(hooks, List):
            logger.error(f"Invalid hooks format: {hooks}")
            return

        for hook in hooks:
            if isinstance(hook, Text):
                # format 1: ["${func()}"]
                logger.debug(f"call hook function: {hook}")
                parse_data(hook, step_variables, self.__project_meta.functions)
            elif isinstance(hook, Dict) and len(hook) == 1:
                # format 2: {"var": "${func()}"}
                var_name, hook_content = list(hook.items())[0]
                hook_content_eval = parse_data(
                    hook_content, step_variables, self.__project_meta.functions
                )
                logger.debug(
                    f"call hook function: {hook_content}, got value: {hook_content_eval}"
                )
                logger.debug(f"assign variable: {var_name} = {hook_content_eval}")
                step_variables[var_name] = hook_content_eval
            else:
                logger.error(f"Invalid hook format: {hook}")

    def __run_step_request(self, step: TStep) -> StepData:
        """run teststep: request"""
        step_data = StepData(name=step.name)

        # parse
        prepare_upload_step(step, self.__project_meta.functions)
        request_dict = step.request.model_dump()
        request_dict.pop("upload", None)
        parsed_request_dict = parse_data(
            request_dict, step.variables, self.__project_meta.functions
        )
        parsed_request_dict["headers"].setdefault(
            "HRUN-Request-ID",
            f"HRUN-{self.__case_id}-{str(int(time.time() * 1000))[-6:]}",
        )
        step.variables["request"] = parsed_request_dict

        # setup hooks
        if step.setup_hooks:
            self.__call_hooks(step.setup_hooks, step.variables, "setup request")

        # prepare arguments
        method = parsed_request_dict.pop("method")
        url_path = parsed_request_dict.pop("url")
        url = build_url(self.__config.base_url, url_path)
        parsed_request_dict["verify"] = self.__config.verify
        parsed_request_dict["json"] = parsed_request_dict.pop("req_json", {})

        # request
        resp = self.__session.request(method, url, **parsed_request_dict)
        resp_obj = ResponseObject(resp)
        step.variables["response"] = resp_obj

        def log_req_resp_details():
            err_msg = "\n{} DETAILED REQUEST & RESPONSE {}\n".format("*" * 32, "*" * 32)

            # log request
            err_msg += "====== request details ======\n"
            err_msg += f"url: {url}\n"
            err_msg += f"method: {method}\n"
            headers = parsed_request_dict.get("headers", {})
            err_msg += f"headers: {headers}\n"
            for k, v in parsed_request_dict.items():
                if k != "headers":
                    v = utils.omit_long_data(v)
                    err_msg += f"{k}: {repr(v)}\n"

            err_msg += "\n"

            # log response
            err_msg += "====== response details ======\n"
            err_msg += f"status_code: {resp.status_code}\n"
            err_msg += f"headers: {resp.headers}\n"
            err_msg += f"body: {repr(resp.text)}\n"
            logger.error(err_msg)

        # teardown hooks（即使请求失败也要执行清理工作）
        if step.teardown_hooks:
            self.__call_hooks(step.teardown_hooks, step.variables, "teardown request")

        # 检查HTTP状态码，如果是错误状态码（4xx, 5xx），立即停止执行
        if resp.status_code >= 400:
            error_msg = f"HTTP {resp.status_code} Error: {resp.reason} for url: {url}"
            logger.error(error_msg)
            # 设置失败状态
            self.success = False
            step_data.success = False
            # 保存步骤数据（深拷贝确保每步快照独立）
            if hasattr(self.__session, "data"):
                self.__session.data.success = False
                step_data.data = copy.deepcopy(self.__session.data)
            # 抛出异常停止后续执行（extract、validate等步骤）
            raise exceptions.RequestFailure(error_msg)

        # extract
        extractors = step.extract
        extract_mapping = resp_obj.extract(extractors, step.variables, self.__project_meta.functions)
        step_data.export_vars = extract_mapping

        variables_mapping = step.variables
        variables_mapping.update(extract_mapping)

        # validate
        validators = step.validators
        session_success = False
        try:
            resp_obj.validate(
                validators, variables_mapping, self.__project_meta.functions
            )
            session_success = True
        except ValidationFailure:
            session_success = False
            log_req_resp_details()
            # log testcase duration before raise ValidationFailure
            self.__duration = time.time() - self.__start_at
            raise
        finally:
            self.success = session_success
            step_data.success = session_success

            if hasattr(self.__session, "data"):
                # httprunner.client.HttpSession, not locust.clients.HttpSession
                # save request & response meta data
                self.__session.data.success = session_success
                self.__session.data.validators = resp_obj.validation_results

                # 深拷贝确保每步数据快照独立，避免共享同一 SessionData 引用
                step_data.data = copy.deepcopy(self.__session.data)

        return step_data

    def __run_step_testcase(self, step: TStep) -> StepData:
        """run teststep: referenced testcase"""
        step_data = StepData(name=step.name)
        step_variables = step.variables
        step_export = step.export

        # setup hooks
        if step.setup_hooks:
            self.__call_hooks(step.setup_hooks, step_variables, "setup testcase")

        if hasattr(step.testcase, "config") and hasattr(step.testcase, "teststeps"):
            testcase_cls = step.testcase
            case_result = (
                testcase_cls()
                .with_session(self.__session)
                .with_case_id(self.__case_id)
                .with_variables(step_variables)
                .with_export(step_export)
                .run()
            )

        elif isinstance(step.testcase, Text):
            if os.path.isabs(step.testcase):
                ref_testcase_path = step.testcase
            else:
                ref_testcase_path = os.path.join(
                    self.__project_meta.RootDir, step.testcase
                )

            case_result = (
                HttpRunner()
                .with_session(self.__session)
                .with_case_id(self.__case_id)
                .with_variables(step_variables)
                .with_export(step_export)
                .run_path(ref_testcase_path)
            )

        else:
            raise exceptions.ParamsError(
                f"Invalid teststep referenced testcase: {step.model_dump()}"
            )

        # teardown hooks
        if step.teardown_hooks:
            self.__call_hooks(step.teardown_hooks, step.variables, "teardown testcase")

        step_data.data = case_result.get_step_datas()  # list of step data
        step_data.export_vars = case_result.get_export_variables()
        step_data.success = case_result.success
        self.success = case_result.success

        if step_data.export_vars:
            logger.info(f"export variables: {step_data.export_vars}")

        return step_data

    def __run_step(self, step: TStep) -> Dict:
        """run teststep, teststep maybe a request or referenced testcase"""
        logger.info(f"run step begin: {step.name} >>>>>>")

        step_data = None
        try:
            if step.request:
                step_data = self.__run_step_request(step)
            elif step.testcase:
                step_data = self.__run_step_testcase(step)
            else:
                raise ParamsError(
                    f"teststep is neither a request nor a referenced testcase: {step.model_dump()}"
                )

            self.__step_datas.append(step_data)
            logger.info(f"run step end: {step.name} <<<<<<\n")
            return step_data.export_vars
        except (exceptions.RequestFailure, ValidationFailure) as e:
            # 当RequestFailure或ValidationFailure异常时，step_data可能已创建但未返回
            # 需要确保step_data被保存
            if step_data is None and step.request:
                # __run_step_request 抛出异常前未返回 step_data，手动构造
                step_data = StepData(name=step.name)
                step_data.success = False
                step_data.error = str(e)
                # 深拷贝当前 session 数据（此时 req_resps 包含本步骤的实际 HTTP 响应）
                if hasattr(self.__session, "data") and self.__session.data:
                    step_data.data = copy.deepcopy(self.__session.data)
                self.__step_datas.append(step_data)
            elif step_data is not None:
                step_data.error = str(e)
                self.__step_datas.append(step_data)
            # 重新抛出异常，让run_testcase决定是否继续执行
            raise
        except Exception as e:
            # 其他异常（如变量替换失败）：无论 step_data 是否创建，都必须追加记录
            # 确保 step_datas 的长度与 teststeps 一一对应，前端依赖此顺序取最后一条
            if step_data is not None:
                step_data.error = str(e)
                self.__step_datas.append(step_data)
            else:
                # 步骤在发起 HTTP 请求之前就已失败（如变量未找到），
                # 创建一个空的失败记录占位，使索引不错位
                failed_step_data = StepData(name=step.name)
                failed_step_data.success = False
                failed_step_data.error = str(e)
                # 如果请求已经拿到响应但在提取/解析阶段失败，保留响应快照供执行记录查看。
                if hasattr(self.__session, "data") and self.__session.data:
                    failed_step_data.data = copy.deepcopy(self.__session.data)
                self.__step_datas.append(failed_step_data)
            # 重新抛出异常
            raise

    def __parse_config(self, config: TConfig):
        config.variables.update(self.__session_variables)
        config.variables = parse_variables_mapping(
            config.variables, self.__project_meta.functions
        )
        config.name = parse_data(
            config.name, config.variables, self.__project_meta.functions
        )
        config.base_url = parse_data(
            config.base_url, config.variables, self.__project_meta.functions
        )

    def run_testcase(self, testcase: TestCase) -> "HttpRunner":
        """run specified testcase

        Examples:
            >>> testcase_obj = TestCase(config=TConfig(...), teststeps=[TStep(...)])
            >>> HttpRunner().with_project_meta(project_meta).run_testcase(testcase_obj)

        """
        self.__config = testcase.config
        self.__teststeps = testcase.teststeps

        # prepare
        self.__project_meta = self.__project_meta or load_project_meta(
            self.__config.path
        )
        self.__parse_config(self.__config)
        self.__start_at = time.time()
        self.__step_datas: List[StepData] = []
        self.__session = self.__session or HttpSession()
        self.__last_failure = None  # 用于 run 结束后重抛，确保 Pytest/Allure 能收到失败状态
        # save extracted variables of teststeps
        extracted_variables: VariablesMapping = {}

        # run teststeps
        try:
            for step in self.__teststeps:
                # override variables
                # step variables > extracted variables from previous steps
                step.variables = merge_variables(step.variables, extracted_variables)
                # step variables > testcase config variables
                step.variables = merge_variables(step.variables, self.__config.variables)

                # parse variables
                step.variables = parse_variables_mapping(
                    step.variables, self.__project_meta.functions
                )

                # run step - 捕获异常以允许后续step继续执行
                try:
                    if USE_ALLURE:
                        with allure.step(f"step: {step.name}"):
                            extract_mapping = self.__run_step(step)
                    else:
                        extract_mapping = self.__run_step(step)

                    # save extracted variables to session variables（只有成功时才更新）
                    extracted_variables.update(extract_mapping)
                except (exceptions.RequestFailure, ValidationFailure) as e:
                    # RequestFailure和ValidationFailure异常已在__run_step中处理，step_data已保存
                    self.__last_failure = e  # 记录最后一次失败，run 结束后重抛以触发 Pytest Fail
                    logger.warning(f"Step '{step.name}' failed ({type(e).__name__}), but continuing with next steps: {str(e)}")
                    # 不更新extracted_variables，因为失败的step可能没有提取到变量
                    continue
                except Exception as e:
                    # 其他异常也继续执行，但记录错误
                    logger.error(f"Step '{step.name}' encountered an error, but continuing with next steps: {str(e)}")
                    # 提取、变量替换等非断言异常也必须影响整条场景结果，避免出现步骤失败但场景误报成功。
                    self.success = False
                    self.__last_failure = e
                    # 尝试保存step_data（如果__run_step中已创建）
                    try:
                        # 如果__run_step中已创建step_data，它应该已经被添加到__step_datas
                        # 这里只是记录错误，继续执行
                        pass
                    except:
                        pass
                    continue

            self.__session_variables.update(extracted_variables)
            # 强制断言同步：若有步骤失败，必须重抛以便 Pytest/Allure 记录为 failed，避免假 Pass
            if not self.success and self.__last_failure is not None:
                raise self.__last_failure
        finally:
            # 确保即使发生异常，duration也被设置
            self.__duration = time.time() - self.__start_at
        return self

    def run_path(self, path: Text) -> "HttpRunner":
        if not os.path.isfile(path):
            raise exceptions.ParamsError(f"Invalid testcase path: {path}")

        testcase_obj = load_testcase_file(path)
        return self.run_testcase(testcase_obj)

    def run(self) -> "HttpRunner":
        """ run current testcase

        Examples:
            >>> TestCaseRequestWithFunctions().run()

        """
        self.__init_tests__()
        testcase_obj = TestCase(config=self.__config, teststeps=self.__teststeps)
        return self.run_testcase(testcase_obj)

    def get_step_datas(self) -> List[StepData]:
        return self.__step_datas

    def get_export_variables(self) -> Dict:
        # override testcase export vars with step export
        export_var_names = self.__export or self.__config.export
        export_vars_mapping = {}
        for var_name in export_var_names:
            if var_name not in self.__session_variables:
                raise ParamsError(
                    f"failed to export variable {var_name} from session variables {self.__session_variables}"
                )

            export_vars_mapping[var_name] = self.__session_variables[var_name]

        return export_vars_mapping

    def get_summary(self) -> TestCaseSummary:
        """get testcase result summary"""
        start_at_timestamp = self.__start_at
        start_at_iso_format = datetime.utcfromtimestamp(start_at_timestamp).isoformat()
        return TestCaseSummary(
            name=self.__config.name,
            success=self.success,
            case_id=self.__case_id,
            time=TestCaseTime(
                start_at=self.__start_at,
                start_at_iso_format=start_at_iso_format,
                duration=self.__duration,
            ),
            in_out=TestCaseInOut(
                config_vars=self.__config.variables,
                export_vars=self.get_export_variables(),
            ),
            log=self.__log_path,
            step_datas=self.__step_datas,
        )

    def test_start(self, param: Dict = None) -> "HttpRunner":
        """main entrance, discovered by pytest"""
        self.__init_tests__()
        self.__project_meta = self.__project_meta or load_project_meta(
            self.__config.path
        )
        if not self.__case_id and getattr(self.__config, "path", None):
            m = re.search(r"test_case_(\d+)", self.__config.path)
            if m:
                self.__case_id = m.group(1)
        self.__case_id = self.__case_id or str(uuid.uuid4())
        self.__log_path = self.__log_path or os.path.join(
            self.__project_meta.RootDir, "logs", f"{self.__case_id}.run.log"
        )
        log_handler = logger.add(self.__log_path, level="DEBUG")

        # parse config name
        config_variables = self.__config.variables
        if param:
            config_variables.update(param)
        config_variables.update(self.__session_variables)
        self.__config.name = parse_data(
            self.__config.name, config_variables, self.__project_meta.functions
        )

        if USE_ALLURE:
            # update allure report meta
            allure.dynamic.title(self.__config.name)
            allure.dynamic.description(f"TestCase ID: {self.__case_id}")

        logger.info(
            f"Start to run testcase: {self.__config.name}, TestCase ID: {self.__case_id}"
        )

        try:
            return self.run_testcase(
                TestCase(config=self.__config, teststeps=self.__teststeps)
            )
        finally:
            logger.remove(log_handler)
            logger.info(f"generate testcase log: {self.__log_path}")
