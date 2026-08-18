package ai.equiedge.mobile.overlay

import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import org.json.JSONObject

/**
 * Same server contract as mobile/src/api/client.ts (React Native) and
 * mobile/ios-share-extension/.../ShareViewController.swift (iOS). All
 * three platforms upload a screenshot to the same POST /mobile/analyze
 * and expect the same {"winrate": number, "action": string} |
 * {"error": string} response shape. Keep them in sync.
 *
 * Uses plain HttpURLConnection rather than a networking library (OkHttp,
 * Retrofit, ...) deliberately — this module ships as source to be dropped
 * into the host app's Gradle project, and a hand-authored source drop
 * should not silently require dependencies the host project may not have
 * declared yet.
 */
object AnalyzeApi {

    private const val ENDPOINT = "https://equiedge-ai.onrender.com/mobile/analyze"
    private const val TIMEOUT_MS = 12_000

    sealed class Result {
        data class Success(val winrate: Double, val action: String) : Result()
        data class Failure(val message: String) : Result()
    }

    /** Blocking call — invoke from a background thread/coroutine, never the main thread. */
    fun analyze(imageBytes: ByteArray): Result {
        val boundary = "EquiEdgeBoundary-${UUID.randomUUID()}"
        val isPng = imageBytes.size >= 4 &&
            imageBytes[0] == 0x89.toByte() && imageBytes[1] == 0x50.toByte() &&
            imageBytes[2] == 0x4E.toByte() && imageBytes[3] == 0x47.toByte()
        val filename = if (isPng) "table.png" else "table.jpg"
        val contentType = if (isPng) "image/png" else "image/jpeg"

        var connection: HttpURLConnection? = null
        try {
            connection = (URL(ENDPOINT).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = TIMEOUT_MS
                readTimeout = TIMEOUT_MS
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            }

            connection.outputStream.use { out: OutputStream ->
                out.write("--$boundary\r\n".toByteArray())
                out.write(
                    "Content-Disposition: form-data; name=\"image\"; filename=\"$filename\"\r\n"
                        .toByteArray()
                )
                out.write("Content-Type: $contentType\r\n\r\n".toByteArray())
                out.write(imageBytes)
                out.write("\r\n--$boundary--\r\n".toByteArray())
            }

            val status = connection.responseCode
            val body = (if (status in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()?.use { it.readText() }
                ?: return Result.Failure("Analysis failed. Try again.")

            val json = try {
                JSONObject(body)
            } catch (e: Exception) {
                return Result.Failure("Analysis failed. Try again.")
            }

            if (status !in 200..299) {
                return Result.Failure(json.optString("error", "Analysis failed. Try again."))
            }

            if (!json.has("winrate") || !json.has("action")) {
                return Result.Failure("Analysis failed. Try again.")
            }
            return Result.Success(json.getDouble("winrate"), json.getString("action"))
        } catch (e: java.net.SocketTimeoutException) {
            return Result.Failure("Analysis timed out. Try again.")
        } catch (e: java.io.IOException) {
            return Result.Failure("Could not reach the server. Check your connection and try again.")
        } finally {
            connection?.disconnect()
        }
    }
}
