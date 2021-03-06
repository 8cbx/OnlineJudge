# coding=utf-8
import os
import json
import commands
import hashlib
import judger
from multiprocessing import Pool

from settings import max_running_number, lrun_gid, lrun_uid, judger_workspace
from language import languages
from result import result
from judge_exceptions import JudgeClientError
from logger import logger


# 下面这个函数作为代理访问实例变量，否则Python2会报错，是Python2的已知问题
# http://stackoverflow.com/questions/1816958/cant-pickle-type-instancemethod-when-using-pythons-multiprocessing-pool-ma/7309686
def _run(instance, test_case_id):
    return instance._judge_one(test_case_id)


class JudgeClient(object):
    def __init__(self, language_code, exe_path, max_cpu_time,  max_memory, test_case_dir, judge_base_path):
        """
        :param language_code: 语言编号
        :param exe_path: 可执行文件路径
        :param max_cpu_time: 最大cpu时间，单位ms
        :param max_memory: 最大内存，单位字节，直接传给judger.run方法
        :param test_case_dir: 测试用例文件夹路径
        :return:返回结果list
        """
        self._language = languages[language_code]
        self._exe_path = exe_path
        self._max_cpu_time = max_cpu_time
        self._max_memory = max_memory
        self._test_case_dir = test_case_dir
        # 进程池
        self._pool = Pool(processes=max_running_number)
        # 测试用例配置项
        self._test_case_info = self._load_test_case_info()
        self._judge_base_path = judge_base_path

    def _load_test_case_info(self):
        # 读取测试用例信息 转换为dict
        try:
            f = open(self._test_case_dir + "info")
            return json.loads(f.read())
        except IOError:
            raise JudgeClientError("Test case config file not found")
        except ValueError:
            raise JudgeClientError("Test case config file format error")

    def _compare_output(self, test_case_id):
        test_case_config = self._test_case_info["test_cases"][str(test_case_id)]
        output_path = os.path.join(self._judge_base_path, str(test_case_id) + ".out")

        try:
            f = open(output_path, "rb")
        except IOError:
            # 文件不存在等引发的异常 返回结果错误
            return "", False

        if "striped_output_md5" not in test_case_config:
            # 计算输出文件的md5 和之前测试用例文件的md5进行比较
            # 兼容之前没有striped_output_md5的测试用例
            # 现在比较的是完整的文件
            md5 = hashlib.md5()
            while True:
                data = f.read(2 ** 8)
                if not data:
                    break
                md5.update(data)
            output_md5 = md5.hexdigest()

            return output_md5, output_md5 == test_case_config["output_md5"]
        else:
            # 这时候需要去除用户输出最后的空格和换行 再去比较md5
            md5 = hashlib.md5()
            # 比较和返回去除空格后的md5比较结果
            md5.update(f.read().rstrip())
            output_md5 = md5.hexdigest()
            return output_md5, output_md5 == test_case_config["striped_output_md5"]

    def _judge_one(self, test_case_id):
        execute_command = self._language["execute_command"].format(exe_path=self._exe_path).split(" ")

        run_result = judger.run(path=execute_command[0],
                                max_cpu_time=self._max_cpu_time,
                                max_memory=self._max_memory,
                                in_file=os.path.join(self._test_case_dir, str(test_case_id) + ".in"),
                                out_file=os.path.join(self._judge_base_path, str(test_case_id) + ".out"),
                                args=execute_command[1:],
                                env=["PATH=" + os.environ["PATH"]],
                                use_sandbox=self._language["use_sandbox"],
                                use_nobody=True)
        run_result["test_case"] = test_case_id
        if run_result["flag"] == 0:
            output_md5, r = self._compare_output(test_case_id)
            if r:
                run_result["result"] = result["accepted"]
            else:
                run_result["result"] = result["wrong_answer"]
            run_result["output_md5"] = output_md5
        elif run_result["flag"] in [1, 2]:
            run_result["result"] = result["time_limit_exceeded"]
        elif run_result["flag"] == 3:
            run_result["result"] = result["memory_limit_exceeded"]
        elif run_result["flag"] == 4:
            run_result["result"] = result["runtime_error"]
        elif run_result["flag"] == 5:
            run_result["result"] = result["system_error"]
        return run_result

    def run(self):
        # 添加到任务队列
        _results = []
        results = []
        for i in range(self._test_case_info["test_case_number"]):
            _results.append(self._pool.apply_async(_run, (self, i + 1)))
        self._pool.close()
        self._pool.join()
        for item in _results:
            # 注意多进程中的异常只有在get()的时候才会被引发
            # http://stackoverflow.com/questions/22094852/how-to-catch-exceptions-in-workers-in-multiprocessing
            try:
                results.append(item.get())
            except Exception as e:
                logger.error("system error")
                logger.error(e)
                results.append({"result": result["system_error"]})
        return results

    def __getstate__(self):
        # 不同的pool之间进行pickle的时候要排除自己，否则报错
        # http://stackoverflow.com/questions/25382455/python-notimplementederror-pool-objects-cannot-be-passed-between-processes
        self_dict = self.__dict__.copy()
        del self_dict['_pool']
        return self_dict
