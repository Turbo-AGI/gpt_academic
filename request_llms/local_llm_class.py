import time
import threading
from toolbox import update_ui
from multiprocessing import Process, Pipe
from contextlib import redirect_stdout


def SingletonLocalLLM(cls):
    """
    一个单实例装饰器
    """
    _instance = {}

    def _singleton(*args, **kargs):
        if cls not in _instance:
            _instance[cls] = cls(*args, **kargs)
            return _instance[cls]
        elif _instance[cls].corrupted:
            _instance[cls] = cls(*args, **kargs)
            return _instance[cls]
        else:
            return _instance[cls]
    return _singleton


def reset_tqdm_output():
    import sys, tqdm
    def status_printer(self, file):
        fp = file
        if fp in (sys.stderr, sys.stdout):
            getattr(sys.stderr, 'flush', lambda: None)()
            getattr(sys.stdout, 'flush', lambda: None)()

        def fp_write(s):
            print(s)
        last_len = [0]

        def print_status(s):
            from tqdm.utils import disp_len
            len_s = disp_len(s)
            fp_write('\r' + s + (' ' * max(last_len[0] - len_s, 0)))
            last_len[0] = len_s
        return print_status
    tqdm.tqdm.status_printer = status_printer


class LocalLLMHandle(Process):
    def __init__(self):
        # ⭐主进程执行
        super().__init__(daemon=True)
        self.corrupted = False
        self.load_model_info()
        self.parent, self.child = Pipe()
        # allow redirect_stdout
        self.std_tag = "[Subprocess Message] "
        self.child.write = lambda x: self.child.send(self.std_tag + x)
        self.running = True
        self._model = None
        self._tokenizer = None
        self.info = ""
        self.check_dependency()
        self.start()
        self.threadLock = threading.Lock()

    def load_model_info(self):
        # 🏃‍♂️🏃‍♂️🏃‍♂️ 子进程执行
        raise NotImplementedError("Method not implemented yet")
        self.model_name = ""
        self.cmd_to_install = ""

    def load_model_and_tokenizer(self):
        """
        This function should return the model and the tokenizer
        """
        # 🏃‍♂️🏃‍♂️🏃‍♂️ 子进程执行
        raise NotImplementedError("Method not implemented yet")

    def llm_stream_generator(self, **kwargs):
        # 🏃‍♂️🏃‍♂️🏃‍♂️ 子进程执行
        raise NotImplementedError("Method not implemented yet")

    def try_to_import_special_deps(self, **kwargs):
        """
        import something that will raise error if the user does not install requirement_*.txt
        """
        # ⭐主进程执行
        raise NotImplementedError("Method not implemented yet")

    def check_dependency(self):
        # ⭐主进程执行
        try:
            self.try_to_import_special_deps()
            self.info = "`依赖检测通过`"
            self.running = True
        except:
            self.info = f"缺少{self.model_name}的依赖，如果要使用{self.model_name}，除了基础的pip依赖以外，您还需要运行{self.cmd_to_install}安装{self.model_name}的依赖。"
            self.running = False

    def run(self):
        # 🏃‍♂️🏃‍♂️🏃‍♂️ 子进程执行
        # 第一次运行，加载参数
        reset_tqdm_output()
        self.info = "`尝试加载模型`"
        try:
            with redirect_stdout(self.child):
                self._model, self._tokenizer = self.load_model_and_tokenizer()
        except:
            self.info = "`加载模型失败`"
            self.running = False
            from toolbox import trimmed_format_exc
            self.child.send(
                f'[Local Message] 不能正常加载{self.model_name}的参数.' + '\n```\n' + trimmed_format_exc() + '\n```\n')
            self.child.send('[FinishBad]')
            raise RuntimeError(f"不能正常加载{self.model_name}的参数！")

        self.info = "`准备就绪`"
        while True:
            # 进入任务等待状态
            kwargs = self.child.recv()
            # 收到消息，开始请求
            try:
                for response_full in self.llm_stream_generator(**kwargs):
                    self.child.send(response_full)
                self.child.send('[Finish]')
                # 请求处理结束，开始下一个循环
            except:
                from toolbox import trimmed_format_exc
                self.child.send(
                    f'[Local Message] 调用{self.model_name}失败.' + '\n```\n' + trimmed_format_exc() + '\n```\n')
                self.child.send('[Finish]')

    def stream_chat(self, **kwargs):
        # ⭐主进程执行
        if self.info == "`准备就绪`":
            yield "`正在等待线程锁，排队中请稍后 ...`"
        with self.threadLock:
            self.parent.send(kwargs)
            std_out = ""
            std_out_clip_len = 4096
            while True:
                res = self.parent.recv()
                if res.startswith(self.std_tag):
                    new_output = res[len(self.std_tag):]
                    std_out = std_out[:std_out_clip_len]
                    print(new_output, end='')
                    std_out = new_output + std_out
                    yield self.std_tag + '\n```\n' + std_out + '\n```\n'
                elif res == '[Finish]':
                    break
                elif res == '[FinishBad]':
                    self.running = False
                    self.corrupted = True
                    break
                else:
                    std_out = ""
                    yield res


def get_local_llm_predict_fns(LLMSingletonClass, model_name, history_format='classic'):
    load_message = f"{model_name}尚未加载，加载需要一段时间。注意，取决于`config.py`的配置，{model_name}消耗大量的内存（CPU）或显存（GPU），也许会导致低配计算机卡死 ……"

    def predict_no_ui_long_connection(inputs, llm_kwargs, history=[], sys_prompt="", observe_window=[], console_slience=False):
        """
            ⭐多线程方法
            函数的说明请见 request_llms/bridge_all.py
        """
        _llm_handle = LLMSingletonClass()
        if len(observe_window) >= 1:
            observe_window[0] = load_message + "\n\n" + _llm_handle.info
        if not _llm_handle.running:
            raise RuntimeError(_llm_handle.info)

        if history_format == 'classic':
            # 没有 sys_prompt 接口，因此把prompt加入 history
            history_feedin = []
            history_feedin.append([sys_prompt, "Certainly!"])
            for i in range(len(history)//2):
                history_feedin.append([history[2*i], history[2*i+1]])
        elif history_format == 'chatglm3':
            # 有 sys_prompt 接口
            conversation_cnt = len(history) // 2
            history_feedin = [{"role": "system", "content": sys_prompt}]
            if conversation_cnt:
                for index in range(0, 2*conversation_cnt, 2):
                    what_i_have_asked = {}
                    what_i_have_asked["role"] = "user"
                    what_i_have_asked["content"] = history[index]
                    what_gpt_answer = {}
                    what_gpt_answer["role"] = "assistant"
                    what_gpt_answer["content"] = history[index+1]
                    if what_i_have_asked["content"] != "":
                        if what_gpt_answer["content"] == "":
                            continue
                        history_feedin.append(what_i_have_asked)
                        history_feedin.append(what_gpt_answer)
                    else:
                        history_feedin[-1]['content'] = what_gpt_answer['content']

        watch_dog_patience = 5  # 看门狗 (watchdog) 的耐心, 设置5秒即可
        response = ""
        for response in _llm_handle.stream_chat(query=inputs, history=history_feedin, max_length=llm_kwargs['max_length'], top_p=llm_kwargs['top_p'], temperature=llm_kwargs['temperature']):
            if len(observe_window) >= 1:
                observe_window[0] = response
            if len(observe_window) >= 2:
                if (time.time()-observe_window[1]) > watch_dog_patience:
                    raise RuntimeError("程序终止。")
        return response

    def predict(inputs, llm_kwargs, plugin_kwargs, chatbot, history=[], system_prompt='', stream=True, additional_fn=None):
        """
            ⭐单线程方法
            函数的说明请见 request_llms/bridge_all.py
        """
        chatbot.append((inputs, ""))

        _llm_handle = LLMSingletonClass()
        chatbot[-1] = (inputs, load_message + "\n\n" + _llm_handle.info)
        yield from update_ui(chatbot=chatbot, history=[])
        if not _llm_handle.running:
            raise RuntimeError(_llm_handle.info)

        if additional_fn is not None:
            from core_functional import handle_core_functionality
            inputs, history = handle_core_functionality(
                additional_fn, inputs, history, chatbot)

        # 处理历史信息
        if history_format == 'classic':
            # 没有 sys_prompt 接口，因此把prompt加入 history
            history_feedin = []
            history_feedin.append([system_prompt, "Certainly!"])
            for i in range(len(history)//2):
                history_feedin.append([history[2*i], history[2*i+1]])
        elif history_format == 'chatglm3':
            # 有 sys_prompt 接口
            conversation_cnt = len(history) // 2
            history_feedin = [{"role": "system", "content": system_prompt}]
            if conversation_cnt:
                for index in range(0, 2*conversation_cnt, 2):
                    what_i_have_asked = {}
                    what_i_have_asked["role"] = "user"
                    what_i_have_asked["content"] = history[index]
                    what_gpt_answer = {}
                    what_gpt_answer["role"] = "assistant"
                    what_gpt_answer["content"] = history[index+1]
                    if what_i_have_asked["content"] != "":
                        if what_gpt_answer["content"] == "":
                            continue
                        history_feedin.append(what_i_have_asked)
                        history_feedin.append(what_gpt_answer)
                    else:
                        history_feedin[-1]['content'] = what_gpt_answer['content']

        # 开始接收回复
        response = f"[Local Message] 等待{model_name}响应中 ..."
        for response in _llm_handle.stream_chat(query=inputs, history=history_feedin, max_length=llm_kwargs['max_length'], top_p=llm_kwargs['top_p'], temperature=llm_kwargs['temperature']):
            chatbot[-1] = (inputs, response)
            yield from update_ui(chatbot=chatbot, history=history)

        # 总结输出
        if response == f"[Local Message] 等待{model_name}响应中 ...":
            response = f"[Local Message] {model_name}响应异常 ..."
        history.extend([inputs, response])
        yield from update_ui(chatbot=chatbot, history=history)

    return predict_no_ui_long_connection, predict
