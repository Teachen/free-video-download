import logging, os
from logging import handlers, Logger

def get_logger(name: str = 'root') -> Logger:
    """
    创建一个日志器的单例对象
    :param name: 日期器名字，默认为root
    :return: 日志器对象
    :return:
    """
    # 1、创建一个logger日期器对象
    logger: Logger = logging.getLogger(name)
    # 2、设置日志级别
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # 3、创建一个控制台处理器对象(FileHandler 要有保存路径)
        th: logging.StreamHandler = logging.StreamHandler()
        try:
            # 创建日志目录
            os.makedirs('logs', exist_ok=True)
        except:
            pass
        rf: handlers.RotatingFileHandler = handlers.RotatingFileHandler(
            filename=f'logs/{name}.log',  # 日志文件名，日志目录 logs 需要手动创建
            mode='a',  # a=append 追加写模式
            maxBytes=300 * 1024 * 1024,  # 最大日志文件大小，单位字节
            encoding='utf-8',  # 日志文件内容的编码
            backupCount=10,  # 备份日志文件的数量，所有日志数量 = backupCount + filename
        )
        # 4、设置每个 Handler 的日志等级（Handler 的等级会覆盖 logger 的等级）
        th.setLevel(logging.DEBUG)
        rf.setLevel(logging.DEBUG)
        
        # 5、创建下日志的格式器对象formatter
        simple_formatter: logging.Formatter = logging.Formatter(
            fmt="{levelname} {asctime} {pathname}:{lineno} {message}",
            style='{'
        )
        verbose_formatter: logging.Formatter = logging.Formatter(
            fmt="【{name}】{levelname} {asctime} {pathname}:{lineno} {message}",
            datefmt="%Y-%m-%d %H:%M:%S",
            style="{"
        )

        # 6、设置下每个Handler的格式器
        th.setFormatter(simple_formatter)
        rf.setFormatter(verbose_formatter)

        # 7、添加下每个Handler到logger
        logger.addHandler(th)
        logger.addHandler(rf)
    return logger

if __name__ == '__main__':
    logger.info("这里是常规运行日志")
    logger.debug("开发人员在调试程序时自己手动打印的日志")
    logger.warning("这里是程序遇到未来会废弃的函数/方法时，输出的警告日志")
    logger.error("这里是程序发生错误时输出的日志")
    logger.critical("这是致命级别的日志，需要紧急修复的")

    print(id(logger1), id(logger))


     