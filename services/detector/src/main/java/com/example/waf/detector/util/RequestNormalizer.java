package com.example.waf.detector.util;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

public final class RequestNormalizer {
    private static final Pattern SQL_BLOCK_COMMENT = Pattern.compile("/\\*[^\\r\\n]{0,512}?\\*/");

    private RequestNormalizer() {}

    public static String normalize(String value, boolean plusAsSpace) {
        String result = value == null ? "" : value;
        for (int i = 0; i < 2; i++) {
            String next = decodePercent(result, plusAsSpace);
            next = decodeHtml(next);
            if (next.equals(result)) break;
            result = next;
        }
        return result.length() <= 16_384 ? result : result.substring(0, 16_384);
    }

    public static String normalizeSql(String value) {
        return SQL_BLOCK_COMMENT.matcher(value).replaceAll(" ");
    }

    private static String decodePercent(String value, boolean plusAsSpace) {
        StringBuilder result = new StringBuilder(value.length());
        for (int index = 0; index < value.length();) {
            char current = value.charAt(index);
            if (current == '+' && plusAsSpace) {
                result.append(' ');
                index++;
            } else if (current == '%' && index + 2 < value.length()
                    && hex(value.charAt(index + 1)) >= 0 && hex(value.charAt(index + 2)) >= 0) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                while (index + 2 < value.length() && value.charAt(index) == '%'
                        && hex(value.charAt(index + 1)) >= 0 && hex(value.charAt(index + 2)) >= 0) {
                    bytes.write(hex(value.charAt(index + 1)) * 16 + hex(value.charAt(index + 2)));
                    index += 3;
                }
                result.append(bytes.toString(StandardCharsets.UTF_8));
            } else {
                result.append(current);
                index++;
            }
        }
        return result.toString();
    }

    private static int hex(char value) {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    }

    private static String decodeHtml(String value) {
        return value.replaceAll("(?i)&lt;", "<")
                .replaceAll("(?i)&gt;", ">")
                .replaceAll("(?i)&quot;", "\\\"")
                .replaceAll("(?i)&#39;|&#x27;", "'")
                .replaceAll("(?i)&amp;", "&");
    }
}
