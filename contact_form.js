import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { SESClient, SendEmailCommand } from "@aws-sdk/client-ses";
import { randomUUID } from "crypto";

const dynamo = new DynamoDBClient({});
const ses = new SESClient({});

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Allow-Methods": "POST,OPTIONS"
};

export const handler = async (event) => {

  if (event.httpMethod === "OPTIONS") {
    return { statusCode: 200, headers: CORS, body: "" };
  }

  try {
    const body = JSON.parse(event.body);
    const { name, email, message, optIn, formType, submittedAt } = body;
    const submissionId = randomUUID();

    await dynamo.send(new PutItemCommand({
      TableName: "sbf-contact-submissions",
      Item: {
        submissionID: { S: submissionId },
        name:         { S: name },
        email:        { S: email },
        message:      { S: message || "" },
        optIn:        { BOOL: optIn },
        formType:     { S: formType },
        submittedAt:  { S: submittedAt }
      }
    }));

    await ses.send(new SendEmailCommand({
      Source: "newsletter@singlebougiefree.com",
      Destination: { ToAddresses: ["bobokatur.tech@gmail.com"] },
      Message: {
        Subject: { Data: `New SBF Submission — ${formType}` },
        Body: {
          Text: {
            Data: `Name: ${name}\nEmail: ${email}\nMessage: ${message}\nOpt-in: ${optIn}\nSubmitted: ${submittedAt}`
          }
        }
      }
    }));

    return {
      statusCode: 200,
      headers: CORS,
      body: JSON.stringify({ message: "Submission received!" })
    };

  } catch (err) {
    console.error("Lambda error:", err);
    return {
      statusCode: 500,
      headers: CORS,
      body: JSON.stringify({ error: "Something went wrong." })
    };
  }
};
