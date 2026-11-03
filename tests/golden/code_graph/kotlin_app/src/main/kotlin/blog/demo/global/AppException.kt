package blog.demo.global

import blog.demo.config.WebConfig

class AppException(val config: WebConfig) : RuntimeException()
