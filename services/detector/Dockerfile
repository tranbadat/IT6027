# Detector (Java 17): một image cho các role detection, rule-management, repartition, ddos.
# Build chạy toàn bộ unit test; test fail thì image không được tạo.
FROM maven:3.9.11-eclipse-temurin-17 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -q dependency:go-offline
COPY src ./src
RUN mvn -q test
RUN mvn -q -DskipTests package

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /build/target/app.jar /app/app.jar
# /data thuộc user app để named volume rules.json nhận đúng quyền ngay lần mount đầu.
RUN useradd --uid 10001 --create-home app && mkdir -p /data && chown app:app /data
USER app
CMD ["java", "-jar", "/app/app.jar"]
