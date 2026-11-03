package blog.demo.config

import blog.demo.domain.user.UserService
import org.springframework.context.annotation.Configuration

@Configuration
class WebConfig {
    fun userService() = UserService()
}
