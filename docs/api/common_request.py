# -*- coding:utf-8 -*-
import datetime
import json
import sys
import time
import random

import requests


# 美的云端请求
class MideaCloudRequest:
    def __init__(self,deviceId,environment = "uat"):
        if environment == "uat":
            self.get_url = 'https://uat.aimidea.cn:11003/v1/base2pro/data/transmit'
            Host = "uat.aimidea.cn:11003"
        elif environment == "sit":
            self.get_url = 'http://api-speech-sit.aimidea.cn:11003/v1/base2pro/data/transmit'
            Host = "api-speech-sit.aimidea.cn:11003"
        elif environment == "pro":
            self.get_url = 'https://api.aimidea.cn:11003/v1/base2pro/data/transmit'
            Host = "api.aimidea.cn:11003"
        self.headers ={
                       'Content-Type': 'application/x-www-form-urlencoded',
                       'Accept': '*/*',
                       'Host': Host,
                       'Connection': 'keep-alive'
                    }
        self.deviceId = deviceId

    def mic_switch(self,enable = 0):
        # 语音开关
        data = {
                "deviceId":self.deviceId,
                "mic":enable
                    }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/mic',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def set_volume(self,value = 10):
        # 设置音量大小
        data = {
                "deviceId":self.deviceId,
                "volume": value
                    }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/volume',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def set_babyCare(self,enable = 1,timeFrom = "20:00",timeTo = "23:00",actionInterval = 1):
        # 啼哭监护设置
        data={"deviceId":self.deviceId,
              "enable":enable,
              "timeFrom":timeFrom,
              "timeTo":timeTo,
              "actionInterval":actionInterval}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/babyCare/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def multi_wakeup_switch(self,enable = 0):
        # 美的云端唯一唤醒开关
        data = {
                "deviceId":self.deviceId,
                "multiWakeup":enable
                    }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/multiWakeup/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def wakeup_switch(self,wakeupWord):
        # 美的云端唤醒词切换
        print(f"\n当前请求切换唤醒词为{wakeupWord}")
        data = {'mid': '1234567890',
                    'deviceId': self.deviceId,
                    'wakeUpWord':str(wakeupWord)
                    }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/wakeUpWord/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def wakeup_Threshold_switch(self,threshold):
        # 美的云端唤醒词阈值调节
        # [0, 20),低灵敏度
        # [20，40),中低灵敏度
        # [40，60),中灵敏度
        # [60，80),中高灵敏度
        # [80, 100],高灵敏度
        print(f"\n当前请求唤醒阈值为{threshold}")
        data = { "deviceId":self.deviceId,
                  "mid":"54aabf504-6d4d-20220730",
                  "threshold":threshold
                }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/awakeThreshold/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            # print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def fullDuplex_switch(self,onoroff,timeOut):
        # 美的云端自然对话开关和对话超时时间设置
        print(f"\n当前请求自然对话开关：{onoroff}，超时时间：{timeOut}")
        data = { "fullDuplex":onoroff,
                 "timeOut":timeOut,
                 "deviceId": self.deviceId,
                 "mid":"echo-5bba2854-1c49-11ed-9a40-43de5ca55de5"}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/speech/fullDuplex',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            # print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def fullDuplex_switch_new(self,onoroff,timeOut):
        # 美的云端自然对话开关和对话超时时间设置
        print(f"\n当前请求自然对话开关：{onoroff}，超时时间：{timeOut}")
        data = {
        "mid": "1234567890",
        "deviceId": self.deviceId,
        "fullDuplex": onoroff,
        "broadcast": 0,
        "timeOut": timeOut,
        "pickup": 0}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v2/device/speech/fullDuplex',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            # print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def log_set(self,status,logLevel):
        # 美的云端日志开关和日志等级设置
        print(f"\n当前请求日志开关：{status}，日志等级为{logLevel}")
        data = {"deviceId":self.deviceId,
                "logLevel":logLevel,
                "status":status}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/log/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            # print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def wakeupAudio_upload(self,onoroff):
        # 美的云端唤醒音频上传开关
        print(f"\n当前请求唤醒音频上传开关为：{onoroff}")
        data = {"deviceId":self.deviceId,
                "mid":"54aabf504-6d4d-4814",
                "wakeAudioUploadSwitch":onoroff}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/wakeAudioUploadSwitch/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def wakeupAudio_upload_new(self,onoroff):
        # 美的云端唤醒音频上传开关
        print(f"\n当前请求唤醒音频上传开关为：{onoroff}")
        data={ "deviceId":self.deviceId,
               "mid":"84aabf504-6d7d-5879",
               "wakeAudioUploadSwitch":onoroff,
               "awakenAudioTypes":"1,2"}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/device/wakeAudioUploadSwitch/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def accent_switch(self,accentId,enableAccent,mixedResEnable):
        # 美的云端方言切换设置
        # 广东话：cantonese
        # 四川话：sichuanese
        # 山东话：shandonghua
        # 上海话：shanghaihua
        # 河南话：henanhua
        # 闽南话：minnanhua
        print(f"\n当前请求设置的方言为：{accentId}，方言是否开启：{enableAccent}，普通话混合模式：{mixedResEnable}")
        data = {"mid":"111","deviceId":self.deviceId,
                "accentId":accentId,
                "enableAccent":enableAccent,
                "mixedResEnable":mixedResEnable}
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/accent/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def night_mode(self,enable = 0,timeFrom = "09:00",timeTo = "18:00",volume = 0,awakeThreshold = 0):
        #夜间模式设置
        data = {"deviceId":self.deviceId,
                "enable":f"{enable}",
                "mid":"3432423412423",
                "modeType":"1",
                "timeFrom":timeFrom,
                "timeTo":timeTo,
                "nightData":
                    {
                     "currentAwakeThreshold":75,
                     "currentVolume":"20",
                     "volume":f"{volume}",
                     "awakeThreshold":awakeThreshold,}
                }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v1/smartTap/setUnDisturbConfig',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def characterValue_switch(self,voice_type = "稳重男声"):
        # 美的发音人音色切换设置
        if voice_type == "温柔女声": #colmo系列空调
            voiceId = "xiaomei_en"
        elif voice_type == "稳重男声":#colmo系列空调
            voiceId = "colmo"
        elif voice_type == "小芳": #儿童空调
            voiceId = "x3_xiaofang"
        elif voice_type == "逍遥子":#儿童空调
            voiceId = "x2_xiaohou"
        elif voice_type == "一菲":#儿童空调
            voiceId = "x3_yifei"
        elif voice_type == "小蓝":#儿童空调
            voiceId = "xiaolan"
        else:
            print("不存在的音色！！")
            return
        data = {"deviceId": self.deviceId,
                "voiceId": voiceId,
                }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v2/tts/voice/set',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def Proactive_interaction(self,interrupt="False",endssion ="False",tts_long = "False"):
        # 美的主动交互请求
        #"type":表示是否开二轮，0不需要.1需要
        #"protoType":表示新旧广播，0旧广播，1新广播
        #"interrupt":表示是否马上打断，false不是，true是
        if endssion == "Ture":
            type = 0
        else:
            type = 1
        if tts_long == "Ture":
            #text = "主动交互下发：静夜思，李白，床前明月光，疑是地上霜。举头望明月，低头思故乡。"+"译文：皎洁的月光洒到床前，迷离中疑是秋霜一片。仰头观看明月呵明月，低头乡思连翩呵连翩。"+"敕勒歌，敕勒川，阴山下。天似穹庐，笼盖四野。天苍苍，野茫茫，风吹草低见牛羊。"+"译文：辽阔的敕勒平原，就在阴山脚下。天空如同毡制的圆顶帐篷，笼罩着草原的四面八方。天空青苍蔚蓝，草原辽阔无边。一阵风吹过，牧草低伏，显露出原来隐没于草丛中的牛羊。",
            text = "主人，早上好，为您推送今天的动态：今天合肥市全天晴，气温-5℃ ~ 5℃，空气质量良，有东北风微风，天气寒冷，注意保暖。 空调当前温度设定为26度,室内温度26度,运行制冷模式,设定风速为自动风"
        else:
            text = "主动交互下发"
        #msgID = "a8e4d56c-6d69-483a-9bb0-92b41c0bfb44"
        msgID = "a8e4d56c-6d69-483a-9bb0-92b41c0bfc31"
        data = { "msgId": msgID,
        "source": "开发调试",
        "content": {
                "type": type,
                "protoType": 0,
                "busType": "other",
                "interrupt": interrupt,
                "volume": 50,
                "broadcasts": [
                {
                        "text": text,
                        "seq": 0,
             "urlType":"tts"
                }
                ]
            },
        "targetDevice": {
                "category": "AC",
                "deviceId": self.deviceId
        },
        "extData": {}
                 }
        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl':'/v2/device/broadcast',
            'data':dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers,data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            #print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

    def Playback_control_interface(self):
        # 美的插件播控接口
        """
        command内容:
        播放模式:orderPlay,listCycle,singleCycle,randomPlay
        上下首：intentPlayerPreviousSong,intentPlayerNextSong
        暂停继续:playerPause,playerResume
        """
        data = {"deviceId": self.deviceId,
                "command": "playerResume",
                "mid": "121100a8-ec56-4721-b8bd-b637a95f0f05"
                }

        dataStr = json.dumps(data, ensure_ascii=False)
        param = {
            'serviceUrl': '/v1/player/control',
            'data': dataStr
        }
        try:
            get_info = requests.post(url=self.get_url, headers=self.headers, data=param)
        except Exception as e:
            print(f"recv()出现错误:{e}")
            return
        if get_info != None:
            # print(f"请求的url:{get_info.url}")
            print(f"响应时间:{get_info.elapsed.total_seconds()}s")
            print(f"响应内容：{get_info.text}")
            print(f'响应状态码：{get_info.status_code}')
            # print(f"请求方法：{get_info.request}")
            # print(f"响应头信息：{get_info.headers}")
            # print(f"请求头信息：{get_info.request.headers}")
            # print(f"cookie信息：{get_info.cookies}")
        return get_info

def time_diff(starttime,nowtime):
    """
    计算两个格式化时间的时间差,时间格式为:2021-11-17 18:54:55.987302
    :param starttime: 开始时间
    :param nowtime: 结束时间
    :return: 返回时间差,float格式
    """
    Time_diff = round((nowtime - starttime).total_seconds(),4)
    return Time_diff

def Random_time(get_time):
    random_time = 1.0
    if "-" in get_time:
        get_time_list = ((get_time.strip("[")).strip("]")).split("-")
        if len(get_time_list) == 2:
            random_time = '{:.1f}'.format(random.uniform(float(get_time_list[0]), float(get_time_list[1])))
        else:
            print("随机时间编写有问题，请检查后再重试！")
    else:
        random_time = get_time
    return float(random_time)

if __name__ == "__main__":
    # str1 = "123ABC345A7B"
    # kw = re.findall(".*([0-9a-fA-F]{6})$",str1)
    # print(kw)

    #1、需要先将设备设为UAT环境下，在美的空调的csk-ap端输入flash.set.int env@1，之后再输入reboot将设备软重启
    #2、在美的空调的csk-ap端输入deviceinfo,查到IOT id的值输入到下面的MideaCloudRequest这个函数里面
    re_func = MideaCloudRequest(178120883777702,environment="sit")
    #语音开关
    #re_func.mic_switch(1)
    #设置音量大小
    #re_func.set_volume(10)
    #啼哭监护设置
    #re_func.set_babyCare(0)
    #夜间模式
   # re_func.night_mode(enable=0)
    #re_func.Proactive_interaction(interrupt="False", endssion="False", tts_long="False")

    # IOT_id = sys.argv[1]
    # interrupt_argv = sys.argv[2]
    # endssion_argv = sys.argv[3]
    # tts_long_argv = sys.argv[4]
    # print(f"IOT_id:{IOT_id},interrupt:{interrupt_argv},endssion:{endssion_argv},tts_long:{tts_long_argv}")
    # re_func = MideaCloudRequest(IOT_id)
    # re_func.Proactive_interaction(interrupt=interrupt_argv, endssion=endssion_argv, tts_long=tts_long_argv)

    #云端主动交互请求
    # for i in [True,False]:
    #     for j in [True,False]:
    #         print(f"主动交互下发：interrupt:{i},endssion:{j}")
    #         re_func.Proactive_interaction(interrupt=i, endssion=j, tts_long=False)
    #         s_time= Random_time("5")
    #         print(f"等待{s_time}s")
    #         time.sleep(s_time)

    #播放控制
    #re_func.Playback_control_interface()

    #切换唤醒词
    #re_func.wakeup_switch("小美小美")
    #re_func.wakeup_switch("你好科慕")

    #设置唤醒词阈值
    # [0, 20),低灵敏度
    # [20，40),中低灵敏度
    # [40，60),中灵敏度
    # [60，80),中高灵敏度
    # [80, 100],高灵敏度
    #re_func.wakeup_Threshold_switch(90)

    #设置自然对话开关和超时时间
    #旧协议
    #re_func.fullDuplex_switch(onoroff=1,timeOut=20)
    #新协议
    #re_func.fullDuplex_switch_new(onoroff=1, timeOut=10)


    #设置日志上传开关和日志等级
    #re_func.log_set(status=0,logLevel=7)


    #唤醒音频上传开关
    #旧协议
    #re_func.wakeupAudio_upload(1)
    #新协议
    #re_func.wakeupAudio_upload_new(1)


    #方言开关设置和设置方言类型
    # 广东话：cantonese
    # 四川话：sichuanese
    # 山东话：shandonghua
    # 上海话：shanghaihua
    # 河南话：henanhua
    # 闽南话：minnanhua
    # 普通话：mandarin
    re_func.accent_switch("sichuanese",enableAccent=1,mixedResEnable=0)


    # 美的发音人音色切换设置
    #re_func.characterValue_switch(voice_type="一菲")

    #唯一唤醒开关
    #re_func.multi_wakeup_switch(1)

    #设置完后，若需要进行正常的交互测试请再在美的空调的csk-ap端输入flash.set.int env@0，将设备设为pro环境，避免在一直处于uat环境下交互功能有异常的问题
