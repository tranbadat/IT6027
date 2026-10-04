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
RUN useradd --uid 10001 --create-home app
USER app
CMD ["java", "-jar", "/app/app.jar"]
