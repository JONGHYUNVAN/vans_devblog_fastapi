package blog.demo.domain.user

import blog.demo.global.ErrorCode.NOT_FOUND

data class User(val id: Long)

object UserDefaults {
    val code = NOT_FOUND
}
