import json


class PMEnvironment:
    def __init__(self, variables: dict):
        self._variables = variables

    def get(self, key: str, default=None):
        return self._variables.get(key, default)

    def set(self, key: str, value):
        self._variables[key] = value

    def unset(self, key: str):
        if key in self._variables:
            del self._variables[key]

    def to_dict(self):
        return dict(self._variables)


class PMResponse:
    def __init__(self, status_code=None, headers=None, body=None, text=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.text = text

    def json(self):
        def to_attr_dict(value):
            if isinstance(value, dict):
                return AttrDict({
                    k: to_attr_dict(v) for k, v in value.items()
                })
            if isinstance(value, list):
                return [to_attr_dict(v) for v in value]
            return value

        if isinstance(self._body, (dict, list)):
            return to_attr_dict(self._body)
        if isinstance(self._body, str):
            try:
                return to_attr_dict(json.loads(self._body))
            except Exception:
                return None
        return None


class AttrDict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def __setattr__(self, key, value):
        self[key] = value


class PMConsole:
    def __init__(self, logs: list):
        self._logs = logs

    def log(self, *args):
        try:
            message = " ".join([str(a) for a in args])
        except Exception:
            message = ""
        self._logs.append(message)


class PMContext:
    def __init__(self, environment_vars: dict, response=None, console_logs=None):
        self.environment = PMEnvironment(environment_vars)
        self.response = response or PMResponse()
        self.console = PMConsole(console_logs if console_logs is not None else [])
